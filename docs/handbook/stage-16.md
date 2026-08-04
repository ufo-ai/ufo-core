# Document, Office, Spreadsheet, Slide, and PDF Processing  `stage-16`

This stage is a toolbox used when the system needs to work on documents on demand, rather than part of a constant main loop. Like a workshop bench, it has separate tools for each file type. The document-review scripts share two common pieces: constants.py names the state and log files, and models.py defines what a review issue looks like. manage_state.py records the review’s progress in JSON, then annotate_pdf.py, annotate_pptx.py, and annotate_xlsx.py read that saved state and place the findings back into PDFs, PowerPoint decks, or Excel cells as visible comments. For Word files, unpack.py opens a DOCX package into editable XML, comment.py prepares Word comment records, pack.py rebuilds the DOCX, and accept_changes.py uses hidden LibreOffice to accept tracked changes. For PowerPoint, unpack.py and pack.py open and rebuild PPTX packages, slides.py cleans or adds slide material, and repair.py fixes known deck problems. For spreadsheets, _soffice.py runs LibreOffice quietly, while recalc.py refreshes formulas and reports errors. The PDF tools fill real form fields, mark up flat forms, and render pages as images.

## Files in this stage

### Document review state and annotations
Shared review models and state management feed format-specific scripts that embed review findings back into PDFs, PowerPoint decks, and Excel workbooks.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a tiny configuration-style file. It does not run any logic by itself. Instead, it acts like a label maker for the document review workflow.

The document review process needs to write information to disk. One file, `document_review_state.json`, is for the current saved state of the review, such as progress that may need to be picked up later. The other file, `review_log.jsonl`, is for logging review events over time. The `.jsonl` ending usually means “JSON Lines”: each line is a separate JSON record, which makes it easy to append new log entries one at a time.

Without this file, each script that needs these filenames might type them out separately. That would make mistakes more likely: one part could write to `review_log.jsonl` while another tries to read `reviews_log.jsonl`, for example. By putting the names here, the rest of the code can import the constants and agree on the same storage locations.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review comment creation`

This file is a small foundation piece for the document review tool. A review issue is something like a spelling problem, a logic concern, or a warning that information may not be public. The `DocumentIssue` type describes the fields every issue is expected to have, such as its ID, severity, description, where it appears, the original text, and any suggested replacement text. Think of it like a standard form that every reported problem must fill out, so later code does not have to guess what information is available.

The file also contains a lookup table that turns internal issue type names, such as `spelling_grammar`, into friendlier labels, such as `Spelling/Grammar`. That keeps comments readable for humans while allowing the program to use stable internal codes.

The main behavior is `format_comment`, which builds the actual text of a review comment. It starts with a header showing the issue type and severity, then includes the issue description. If a suggestion is available and suggestions are enabled, it adds a suggested replacement. Without this file, different parts of the review tool might format issues inconsistently or misunderstand what data an issue is supposed to contain.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: Turns one document review issue into a clear, human-readable comment. Someone would use it when they need to show an issue to a reviewer, optionally including the suggested replacement text.

**Data flow**: It receives an issue record and a yes-or-no setting for whether to include suggestions. It reads the issue type, severity, description, and possibly the suggested new text. It changes the internal issue type code into a friendly label when one is known, builds the comment text in sections, and returns the finished string without changing the issue itself.

**Call relations**: This function is called when review output needs to become a comment a person can read. Inside that flow, it uses the issue record's `get` method to safely check whether `new_text` exists before adding a suggestion, so missing or empty suggestion text does not break the comment.

*Call graph*: 1 external calls (get).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `command-line document review workflow`

This file acts like a checklist and notebook for reviewing a document. A reviewer, or another automated tool, can run commands such as “init”, “add-sections”, “add-claims”, “update-claims”, “add-issues”, and “submit”. Each command reads or changes a shared state file named document_review_state.json. Without this file, the review process would have no reliable memory of which document is being reviewed, what sections were found, which claims still need checking, or what problems were discovered.

The review moves through simple phases: outlining the document, finding claims, fact-checking them, finding issues, and completing the review. The script warns if a command is run out of the expected phase, but it usually continues rather than blocking the user. It also writes a separate JSON-lines log, where each line records one action with a timestamp. That log is useful as an audit trail, like a receipt book showing what happened and when.

Most commands accept structured JSON data either directly through the command line or from a file. Before saving anything, the script checks that required fields are present and that values such as issue severity or claim status are allowed. This keeps the state file consistent, so later commands can safely read it.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the command result as one valid JSON object. This lets other tools read both a friendly message and structured progress information without scraping plain text.

**Data flow**: It receives a message, the current phase, the document name, and optional extra result data. It wraps them into a dictionary under document_review_progress, converts that dictionary to JSON text, and prints it to standard output.

**Call relations**: The commands that change review state call this at the end of their work. After initializing, adding sections, adding claims, updating claims, adding issues, or submitting, they hand their final status to this function so the outside caller gets a predictable machine-readable response.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds one audit entry to the review log. It records what command ran, when it ran, and how the review phase changed.

**Data flow**: It receives the command name, the phase before and after the command, and any extra details. It adds the current UTC time, turns the entry into JSON, and appends it as one line to the log file.

**Call relations**: Most user-facing commands call this after reading or changing state. It does not decide what happened; it records the story that the command gives it, so there is a chronological trail of review activity.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the current review state from disk. It is the starting point for almost every command that needs to know what has already happened.

**Data flow**: It looks for the state JSON file. If the file is missing, it prints an error telling the user to run init first and exits. If the file exists, it reads the JSON text and returns it as a Python dictionary.

**Call relations**: Commands such as add-sections, add-claims, update-claims, add-issues, get-claims, get-issues, status, and submit call this before doing their work. It supplies the shared memory that those commands inspect or modify.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk. It preserves the latest sections, claims, issues, phase, and summary for future commands.

**Data flow**: It receives the whole state dictionary, converts it to nicely formatted JSON, and writes it to the state file. The result is a refreshed document_review_state.json file.

**Call relations**: Commands that change the review call this after making their edits. For example, init creates the first state, add-claims adds new claim records, and submit marks the review complete before this function stores the updated version.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user when they run a command during an unexpected review phase. It is a guardrail, not a locked gate.

**Data flow**: It receives the current state and the phase the command normally expects. If the state’s phase is different, it prints a warning to standard error. It does not change the state and does not stop the command.

**Call relations**: Workflow commands call this near the start of their work. It helps users notice when they are doing steps out of order while still allowing flexible recovery or manual correction.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that a JSON object contains the fields a command needs. This prevents incomplete sections, claims, claim updates, or issues from being saved.

**Data flow**: It receives one item, a list of required field names, and a label such as “Claim” or “Issue”. It looks for missing fields. If any are absent, it prints a clear error and exits; otherwise it returns normally.

**Call relations**: The add and update commands call this before using incoming JSON data. It acts like a form checker before the data is copied into the review state.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a fixed set of allowed words. This keeps fields like claim type, claim status, issue type, and severity consistent.

**Data flow**: It receives a value, a set of allowed values, and the field name for the error message. If the value is not allowed, it prints the valid choices and exits. If it is allowed, nothing is changed.

**Call relations**: Commands that add claims, update claims, or add issues call this while checking input. It keeps later reporting simple because the script can count on a small known vocabulary.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a value into a page number and confirms it is at least 1. It protects section ranges from invalid page numbers.

**Data flow**: It receives a value and a field name. It tries to convert the value to an integer. If conversion fails or the number is less than 1, it prints an error and exits; otherwise it returns the integer.

**Call relations**: cmd_add_sections uses this when reading start_page and end_page. The returned numbers are then saved as the section’s page range.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a value can be used as meaningful text. It allows strings and integers, converts them to text, and rejects empty values.

**Data flow**: It receives a value and a field name. If the value is not a string or integer, or if it becomes blank text, it prints an error and exits. Otherwise it returns the cleaned text form.

**Call relations**: cmd_add_claims and cmd_add_issues use this for required location fields. This makes sure each claim or issue points to somewhere in the document.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional text anchor. An anchor is a specific snippet or marker that can help locate a claim or issue in the document.

**Data flow**: It receives a value and a field name. If the value is null, it returns null. If the value is a non-empty string, it returns it. Anything else causes an error message and exits.

**Call relations**: cmd_add_claims and cmd_add_issues call this when optional anchor data is present. The validated anchor is then saved with the claim or issue.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input from either a command-line argument or a file. This lets users provide small data inline or larger data in a separate file.

**Data flow**: It receives parsed command-line arguments. If a file path was supplied, it reads that file as text. Otherwise it returns the direct data string from the command line.

**Call relations**: The commands that ingest JSON arrays call this before parsing the JSON. It hides the difference between --data and --file so the rest of each command can work with one text string.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates the first state file with an outline phase and empty containers for sections, claims, issues, and summary.

**Data flow**: It receives command-line arguments containing the document filename. It rejects an empty filename, builds a fresh state dictionary, saves it, logs the init action, and prints a JSON result confirming the review has started.

**Call relations**: main calls this when the user runs the init command. It is the one command that does not load an existing state first, because its job is to create that state.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document’s main sections and moves the review into the claim-finding phase. This gives later claims and issues named places to attach to.

**Data flow**: It loads the current state, warns if the review is not in the outline phase, reads a JSON array of sections, and checks each section’s name and page range. It stores the sections by name, changes the phase to find_claims, saves the state, logs the action, and prints a JSON summary.

**Call relations**: main calls this for the add-sections command. It relies on load_state, _resolve_data, and validation helpers before handing the updated state to save_state, log_action, and _emit_result.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in a specific section. These claims become the checklist for later fact-checking.

**Data flow**: It loads the state, warns if the phase is not find_claims, verifies that the named section exists, and reads a JSON array of claims. For each claim, it checks required fields and allowed claim types, creates a new claim ID, marks the claim as unverified, and stores it. It then saves, logs, and prints the created claim summaries.

**Call relations**: main calls this for the add-claims command. It uses validation helpers to keep incoming claim data well-shaped, then uses save_state and log_action to make the additions durable and traceable.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the results of fact-checking existing claims. It marks claims as verified, refuted, or inconclusive and can attach source URLs.

**Data flow**: It loads the state and notes the phase before changes. If the review is still in find_claims, it moves it to fact_check. It reads a JSON array of updates, checks each claim ID and status, increments the claim’s attempt count, appends any source URLs, saves the state, logs counts by status, and prints a JSON result.

**Call relations**: main calls this for the update-claims command. It depends on load_state and validation helpers to find safe targets, then passes the changed state to save_state and reports progress through log_action and _emit_result.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds review problems found in a section, such as factual issues, spelling problems, non-public information, or narrative logic concerns. These records describe what is wrong and what replacement text is suggested.

**Data flow**: It loads the state, warns if the phase is not find_issues, and moves from fact_check to find_issues when appropriate. It confirms the section exists, reads a JSON array of issues, validates each issue’s fields, type, severity, location, and optional anchor, creates issue IDs, stores the issues, saves the state, logs the action, and prints summaries of the new issues.

**Call relations**: main calls this for the add-issues command. It works much like cmd_add_claims, but for problems and proposed fixes rather than facts to verify.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Finishes the review with a written summary. It marks the state as complete so the workflow has a clear endpoint.

**Data flow**: It loads the state, warns if the review is not in the issue-finding phase, and checks that the summary is not empty. It sets the phase to complete, stores the summary, saves the state, counts sections, claims, and issues, logs the submission, and prints a JSON completion message.

**Call relations**: main calls this for the submit command. It is the final state-changing step in the normal workflow and uses the shared save, log, and result-output helpers.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved claims in a readable report, optionally narrowed by claim status or section. This helps a reviewer inspect what still needs checking or what has already been decided.

**Data flow**: It loads the state and starts with all saved claims. If a status or section filter was provided, it keeps only matching claims. It logs the lookup, then prints either a no-results message or a formatted list with IDs, status, type, section, location, text, description, and sources.

**Call relations**: main calls this for the get-claims command. Unlike the state-changing commands, it does not save anything; it reads with load_state and records the lookup with log_action.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved issues in a readable report, optionally narrowed by severity or section. This helps a reviewer review the problems that need attention.

**Data flow**: It loads the state and starts with all saved issues. It applies severity and section filters if present, logs the lookup, then prints either a no-results message or a formatted list with issue IDs, severity, type, section, location, original text, context, description, and suggested replacement text.

**Call relations**: main calls this for the get-issues command. It is a read-only reporting command that uses load_state for data and log_action to leave an audit trail of the query.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Prints a dashboard-style snapshot of the whole review. It shows the current phase and summary counts so the user can quickly see progress.

**Data flow**: It loads the state, prints the document name and current phase, then prints sections if any exist. It counts claims by status and issues by severity and type, and prints the final summary if one has been saved.

**Call relations**: main calls this for the status command. It only relies on load_state and does not log or save, because it simply displays the current contents of the review state.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and routes each command to the right function. It is the front door of the script.

**Data flow**: It builds an argument parser, declares all supported subcommands and their arguments, parses the user’s command line, looks up the matching command function, and calls it with the parsed arguments.

**Call relations**: When the script is run directly, main is called. It does not do review work itself; it dispatches to functions such as cmd_init, cmd_add_sections, cmd_update_claims, and cmd_status, which perform the actual actions.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `document review annotation step`

This file turns a document review report into something a person can see inside the PDF itself. Without it, review issues would stay in a separate JSON file, which is useful for software but awkward for a human reviewer who wants to open the document and see what needs attention.

The script expects two command-line arguments: an input PDF and an output PDF. It also expects a review state file in the current working directory. That state file contains the issues found earlier in the review process. For each issue, the script works out which page it belongs to, formats the issue into a readable comment, and chooses a color based on severity: red for high, orange for medium, and yellow for low.

It then searches the page for the original text connected to the issue. If it finds the text, it highlights it and places a small comment icon next to the highlighted area. If it cannot find the text, it still adds the comment at a default spot on the page, so the issue is not silently lost. This is like a reviewer using a highlighter and sticky notes, except the marks are created automatically.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved document review results and returns the list of issues that should be placed into the PDF. If the expected state file is missing, it stops the script with a clear error instead of continuing with no data.

**Data flow**: It looks for the configured state filename in the current folder. If the file exists, it reads the JSON text, turns it into Python data, takes the values from the issues section, and returns them as a list. If the file is not there, it prints an error message to standard error and exits the program.

**Call relations**: The main annotation flow calls this first, because it needs to know what comments to add before it opens and edits the PDF. It relies on JSON parsing and filesystem path helpers to turn the saved review state into usable issue records.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to locate the text connected to an issue on a PDF page. It uses a longer text snippet first, then falls back to a shorter prefix if the first search fails.

**Data flow**: It receives a PDF page and the original issue text. It searches the page for the first part of that text, up to the primary search length. If nothing is found, it searches again using a shorter starting piece. It returns the matching PDF areas, called quads, which describe where the text appears on the page.

**Call relations**: The annotation function calls this whenever it is ready to mark an issue on a page. Its result decides whether the script can highlight the exact text or must place only a comment icon at a fallback location.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function for the script. It opens the input PDF, adds highlights and comment notes for each review issue, and saves the result as a new PDF.

**Data flow**: It starts by loading the issue list. If there are no issues, it prints a short message and stops. Otherwise, it opens the input PDF, checks each issue's page number, chooses a color from the issue severity, formats the comment text, searches for the original text, and then adds a highlight plus a sticky note when possible. If the text cannot be found, it still adds the note at a default position. At the end, it saves the edited PDF to the output path, closes the file, and prints how many annotations were added.

**Call relations**: This function ties the whole script together. It calls load_issues to get the review data, calls find_quads to locate issue text in the PDF, and calls the shared comment formatter so each note has consistent wording. When the file is run from the command line, the small main block checks the arguments and then hands control to this function.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `post-review export`

A PowerPoint .pptx file is really a zip folder full of XML files. This script uses that fact to add comments directly into the package. It reads review issues from document_review_state.json, groups them by slide number, copies the input presentation to a new output file, opens that output as a zip archive, and adds the XML pieces PowerPoint expects for comments.

The main work is split into a few parts. First, it loads the saved issues and ignores any issue whose location is not a slide number. Then it creates one comment XML file per affected slide. Each issue becomes a PowerPoint comment written by a fixed author, “Flying Object,” with the formatted issue text as the comment body. It also updates each slide’s relationship file, which is like a small address book telling PowerPoint where that slide’s comment file lives.

Finally, it writes the shared comment-author file and updates the presentation’s relationship and content-type files so PowerPoint recognizes the new comment parts. Without these bookkeeping updates, the comment XML might exist in the file but PowerPoint would not know how to find or interpret it. The script cleans up its temporary extracted copy after rebuilding the output PPTX.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: This function reads the review state file and pulls out the saved document issues. It stops the script with a clear error if the expected state file is missing, because there would be no feedback to insert into the presentation.

**Data flow**: It starts with the fixed state filename from the constants module. It checks whether that JSON file exists, reads its text, turns the JSON text into Python data, and returns the values stored under the issues section. If the file is not present, it prints an error to standard error and exits the process.

**Call relations**: The main annotation flow calls this first. Its output becomes the raw list of issues that later gets grouped by slide and turned into PowerPoint comments.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: This function sorts review issues into buckets by slide number. It makes the later XML-writing step simpler, because PowerPoint stores comments separately for each slide.

**Data flow**: It receives a list of issue records. For each issue, it looks at the issue’s location field and tries to read it as a whole number. If that works, the issue is placed under that slide number; if not, the issue is skipped. It returns a dictionary where each slide number points to the list of issues for that slide.

**Call relations**: The main annotate function calls this after loading issues. The grouped result is then passed to the slide-comment writer and to the file-bookkeeping writer, so both know exactly which slides need comment files.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: This helper finds the largest existing relationship ID in a PowerPoint relationship file. Relationship IDs are labels like rId1 or rId2, and the script needs the next free one when adding a new link.

**Data flow**: It receives the path to a .rels XML file. If the file does not exist, it returns 0. If it exists, it parses the XML, scans each relationship’s Id value, extracts any number in it, and returns the highest number found.

**Call relations**: add_relationship calls this right before creating a new relationship. It prevents the script from accidentally reusing an existing ID when it adds a link to a comment file or author file.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: This function adds a link inside a PowerPoint relationship file. In a PPTX, relationship files tell PowerPoint how one part of the package connects to another, like a map saying “this slide’s comments are over there.”

**Data flow**: It receives a relationship-file path, a relationship type, and a target path. It opens the existing XML file or creates a new relationship document if none exists. If the same relationship type is already present, it leaves the file unchanged. Otherwise, it chooses the next available rId number, adds a new Relationship XML entry, and writes the file back to disk.

**Call relations**: Both write_slide_comments and write_author_and_rels rely on this helper. The slide-comment writer uses it to connect slides to their comment XML files, and the author writer uses it to connect the overall presentation to the comment-author file.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: This function creates the actual per-slide comment XML files. Each review issue on a slide becomes a classic PowerPoint comment with text generated from the issue details.

**Data flow**: It receives the temporary extracted PPTX folder and the issues already grouped by slide. For each slide, it creates a comment list XML document, adds one comment element per issue, assigns a shared author ID, timestamp, comment index, default position, and formatted text, then writes that slide’s comment file. It also updates the slide’s relationship file so PowerPoint can find the comment file. It returns the total number of comments it wrote.

**Call relations**: annotate calls this after extracting the copied PPTX. During its work it calls format_comment to turn each issue into readable comment text and add_relationship to attach each comment file to the right slide. Its returned count is later used when writing the author metadata.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: This function writes the shared PowerPoint metadata that makes the comments valid and visible. It defines the comment author and tells the PPTX package what new XML files have been added.

**Data flow**: It receives the temporary extracted PPTX folder, the total number of comments, and the slide grouping. It writes ppt/commentAuthors.xml with the fixed author name and initials. It then adds a presentation-level relationship to that author file. Finally, it opens [Content_Types].xml and adds entries for the author file and each slide comment file if they are not already listed, then saves the updated XML.

**Call relations**: annotate calls this after write_slide_comments has created the comment files. It uses add_relationship for the presentation-to-author link, and it completes the package-level bookkeeping PowerPoint needs before the rebuilt PPTX can show comments correctly.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: This is the main workflow for adding review comments to a PowerPoint file. It takes an input PPTX and produces an output PPTX with the saved issues embedded as comments.

**Data flow**: It starts by loading issues from the review state file. If there are none, it prints a message and stops without modifying anything further. Otherwise, it groups issues by slide, copies the input presentation to the output path, extracts that output file into a temporary folder, writes comment XML and metadata into the extracted package, then zips the folder back into the output PPTX. At the end, it prints how many comments were added and removes the temporary folder even if an error occurs.

**Call relations**: This function ties together the whole script. It calls load_issues, group_by_slide, write_slide_comments, and write_author_and_rels in order, while standard library tools do the file copying, zip extraction, zip rebuilding, folder walking, and cleanup. The command-line block at the bottom calls this function when the script is run directly with input and output filenames.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review export / annotation`

This file solves a practical handoff problem: review issues may be found by an automated document-review process, but spreadsheet users need to see those issues in the spreadsheet itself. The script reads the saved review state from `document_review_state.json`, opens an `.xlsx` file, and attaches each issue as an Excel comment.

It works like a careful note-sticker. First it loads the list of issues. Then it copies the input workbook to the requested output path, so the original file is not changed. For each issue, it turns the issue data into readable comment text using `format_comment`. It then tries several ways to find the right place for that comment: first a named worksheet and exact cell address if the issue provides them, then a search for the original reviewed text inside that worksheet, then the same text search across all worksheets. If none of those work, it falls back to cell A1 on the first worksheet. If A1 already has a fallback comment, it appends the new one rather than replacing it.

The important behavior is that every issue is preserved somewhere. The placement may be precise when the saved issue has enough location data, or approximate when it only has text to search for.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the review issues that were previously saved in the document review state file. It stops the script with a clear error if that file is missing, because there would be nothing reliable to annotate.

**Data flow**: It starts with the expected state file name from `STATE_FILENAME`. It checks whether that file exists, reads its JSON text, turns that text into Python data, and returns the saved issues as a list. If the file is not present, it prints an error to standard error and exits the program.

**Call relations**: The main annotation flow calls `load_issues` first. Its returned issue list becomes the work queue that `annotate` loops over to create Excel comments.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell that contains a given piece of text. It is used when the issue does not have a trustworthy exact cell address, but does remember the original text that was reviewed.

**Data flow**: It receives a worksheet and some target text. It normalizes the target by trimming spaces and making it lowercase, then checks every non-empty cell in the worksheet for that text, also normalized. It returns the first matching cell, or `None` if no cell contains the text.

**Call relations**: `annotate` calls `find_cell` after trying more exact placement. It may be used first inside the worksheet named by the issue, and then across all worksheets if needed.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks up a worksheet by name without caring about uppercase or lowercase differences. It helps connect an issue's saved location, such as a sheet name, to the actual sheet inside the workbook.

**Data flow**: It receives an open workbook and a location string. It compares that string with each worksheet title in lowercase form. It returns the matching worksheet if one is found, or `None` if no worksheet has that name.

**Call relations**: `annotate` calls `find_worksheet` before trying sheet-specific placement. If this succeeds, the script can try an exact cell anchor or a text search within that particular worksheet.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a comment to one exact cell reference, such as `B12`. It keeps invalid or unusable cell addresses from crashing the whole annotation run.

**Data flow**: It receives a worksheet, a cell address, and an Excel comment object. It tries to fetch that cell and assign the comment to it. If the cell reference works, it returns `True`; if the reference is invalid, it catches the error and returns `False`.

**Call relations**: `annotate` calls `_place_on_cell` when an issue names both a worksheet and an anchor cell. If this direct placement fails, `annotate` falls back to searching for the original text.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It copies an input Excel file, opens the copy, and adds one comment for each saved review issue in the best cell it can find.

**Data flow**: It receives an input workbook path and an output workbook path. It loads issues, copies the input file to the output file, opens that output file with `openpyxl`, and loops through each issue. For each issue, it formats the comment text, creates an Excel comment, tries exact placement, then worksheet text search, then whole-workbook text search, and finally cell A1 as a fallback. At the end, it saves the modified workbook and prints how many comments were added.

**Call relations**: This function coordinates the whole script. It calls `load_issues` to get review data, `format_comment` to make human-readable comment text, `find_worksheet`, `_place_on_cell`, and `find_cell` to choose where comments belong, and `openpyxl` plus file-copying tools to read and write the spreadsheet.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).


### Word document package editing
DOCX helpers clean tracked changes, unpack Word packages for XML inspection, add comment metadata, and repack the edited folder into a usable document.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `on-demand document processing`

This file solves a practical document-cleanup problem: a DOCX file may contain tracked edits, and another part of the system may need a final version where those edits are accepted. Instead of trying to edit the DOCX format directly, the script asks LibreOffice to do the job, because LibreOffice already understands Word documents and tracked changes.

The script first checks that the input exists and is a .docx file. It then copies the original file to the requested output path, so the source document is not changed. Next, it makes sure LibreOffice has a small Basic macro installed in a temporary LibreOffice profile. A macro is like a tiny built-in script that LibreOffice can run inside a document. This macro tells LibreOffice to accept all tracked changes, save the document, and close it.

Finally, the script launches LibreOffice with that copied output document and asks it to run the macro. One important quirk is handled deliberately: LibreOffice may hang after the macro has already saved the file. Because of that, a timeout is treated as success rather than failure. Without this file, the system would need a more fragile way to remove tracked changes, or users would have to clean documents by hand.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when starting LibreOffice. It forces LibreOffice to use a non-graphical display backend, which helps it run safely in the background on servers.

**Data flow**: It starts with the current process environment variables, copies them, then adds or overwrites the LibreOffice display setting. It returns the modified environment dictionary for subprocess calls.

**Call relations**: Whenever the script starts LibreOffice, the caller asks this helper for the right environment first. `_ensure_macro` uses it while preparing the LibreOffice profile, and `accept_tracked_changes` uses it when running the macro on the document.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: Creates the command-line argument that tells LibreOffice which temporary user profile to use. This keeps the macro and settings separate from any normal LibreOffice profile on the machine.

**Data flow**: It reads the fixed temporary profile path from the module constants and formats it into the special LibreOffice `UserInstallation` argument. The result is a string that can be placed directly in the LibreOffice command.

**Call relations**: This helper is used by both parts of the LibreOffice flow. `_ensure_macro` uses it when initializing or preparing the profile, and `accept_tracked_changes` uses it when launching LibreOffice to process the copied DOCX.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure the LibreOffice macro needed to accept tracked changes is installed in the temporary profile. Think of it as placing the right tool in LibreOffice’s toolbox before asking LibreOffice to work on the document.

**Data flow**: It checks whether the macro file already exists and contains the expected macro name. If not, it may start LibreOffice once to initialize the profile, creates the needed macro directory, writes the macro XML file, and returns `True` to say the macro is ready.

**Call relations**: `accept_tracked_changes` calls this before it tries to process any document. Inside, `_ensure_macro` relies on `_profile_arg` and `_soffice_env` to start LibreOffice correctly, and it uses `subprocess.run` to perform the profile-initialization step when needed.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: Creates an output DOCX where all tracked changes from the input DOCX have been accepted. This is the main reusable function for document cleanup, and it returns a human-readable status message instead of raising errors for common problems.

**Data flow**: It receives an input file path and an output file path. It checks that the input exists and is a DOCX, creates the output folder if needed, copies the input to the output location, makes sure the LibreOffice macro is installed, then starts LibreOffice on the copied file. The returned value is always `None` plus a message saying either what went wrong or that the changes were accepted.

**Call relations**: This is the central flow that the command-line part of the script calls after reading arguments. It hands off environment setup to `_soffice_env`, profile argument creation to `_profile_arg`, macro installation to `_ensure_macro`, file copying to `shutil.copy2`, and actual document editing to LibreOffice through `subprocess.run`.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual DOCX unpacking and cleanup`

A .docx file is really a ZIP archive full of XML files. This file is a small command-line tool that opens that archive, extracts its contents, and makes the XML less painful to read. Without it, anyone trying to work with the inside of a Word document would be stuck with compressed files and Word’s very noisy XML structure.

The main flow is simple: check that the input exists and is a .docx file, create the output folder, unzip everything there, then pretty-print the XML with indentation. After that it focuses on word/document.xml, which is the main body of the document. It can merge nearby Word “runs” with the same formatting. A run is a small stretch of text that Word stores as one unit, often split into many pieces for reasons that are not meaningful to humans. It can also combine adjacent tracked insertions or deletions from the same author, so edits read more like one change instead of many tiny fragments.

Finally, it replaces curly quote characters with XML character entities. That keeps those quote marks visible and explicit in the XML text. The script returns a small result object with counts, plus a human-readable summary message for the command line.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker for the script. It unpacks a .docx file into a folder, formats the XML, optionally simplifies Word’s run and tracked-change markup, and returns both counts and a readable status message.

**Data flow**: It receives an input file path, an output folder path, and two true-or-false options. It checks the file, extracts the ZIP contents, finds XML-like files, rewrites them in a more readable form, optionally cleans word/document.xml, replaces curly quotes, and then returns an UnpackResult plus a summary string. If the input is missing, not a .docx file, or not a valid ZIP archive, it returns no result and an error message.

**Call relations**: When the script is run from the command line, this function is the central action. It calls _indent_xml for each XML file, _coalesce_tracked_changes and _merge_adjacent_runs for the main document when enabled, and _replace_curly_quotes at the end so every extracted XML file gets the same quote treatment.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This makes one XML file easier to read by adding normal indentation and line breaks. It is like taking a cramped paragraph and laying it out as an outline.

**Data flow**: It receives the path to one XML file. It parses the XML, asks lxml to indent it with two spaces, and writes the formatted XML back to the same file. If parsing or writing fails, it quietly leaves the file as it was.

**Call relations**: unpack_docx calls this right after extraction for every .xml and .rels file it finds. It prepares the unpacked files for human inspection before later cleanup steps make targeted changes to document.xml.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This rewrites curly quote characters as explicit XML character references. That makes these special quote marks stand out in the file instead of being hidden as ordinary-looking text characters.

**Data flow**: It receives the path to one XML file, reads it as text, searches for curly single or double quotes, and replaces each one with its numeric XML entity form. If there are no curly quotes, it does nothing; if reading or writing fails, it silently skips the file.

**Call relations**: unpack_docx calls this near the end for every extracted XML-like file. It runs after formatting and document cleanup so the final output consistently represents curly quotes across the unpacked document.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This simplifies the main Word document XML by joining neighboring text runs that have the same formatting. Word often splits text into many runs even when a reader would see one continuous piece of text.

**Data flow**: It receives the path to word/document.xml. It reads the XML, removes proofing-error markers, strips run attributes related to Word revision IDs, finds parent elements that contain runs, and asks _merge_runs_in to combine compatible neighboring runs. If anything was merged, it writes the updated XML back and returns the number of absorbed runs.

**Call relations**: unpack_docx calls this only for word/document.xml and only when run merging is enabled. It delegates the detailed per-container merging to _merge_runs_in, which in turn uses smaller helpers to compare formatting and join text nodes.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This creates a stable fingerprint for a run’s formatting. The script uses that fingerprint to decide whether two neighboring runs can safely be merged.

**Data flow**: It receives one Word run XML element. It looks for that run’s formatting child element, called w:rPr in WordprocessingML, and converts it into a canonical string, meaning a consistent serialized form suitable for comparison. If the run has no formatting element, it returns nothing.

**Call relations**: _merge_runs_in calls this for each run while grouping neighboring runs. Its output is the comparison key that lets the merger say, in effect, 'these two text pieces are dressed the same, so they can become one.'

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This does the actual work of joining adjacent runs inside one parent XML element. It keeps formatting boundaries intact by only merging runs that sit next to each other and share the same formatting fingerprint.

**Data flow**: It receives a container element, such as a paragraph or another Word XML parent. It walks through the container’s children, builds groups of consecutive run elements with matching formatting, moves non-formatting child nodes from later runs into the first run, removes the now-empty later runs, joins neighboring text nodes inside the surviving run, and returns how many runs were absorbed.

**Call relations**: _merge_adjacent_runs calls this once for each parent element that contains runs. It relies on _canonical_rpr to compare formatting and calls _join_adjacent_text after merging so the inside of the surviving run is also cleaned up.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This tidies a merged run by combining text nodes that ended up side by side. After runs are merged, their text pieces may still be separate XML elements even though they now belong together.

**Data flow**: It receives one run element. It scans its children from left to right, and whenever two neighboring children are both Word text elements, it combines their text into the first one and removes the second. If the merged text starts or ends with a space, it marks the XML so that Word preserves that space.

**Call relations**: _merge_runs_in calls this after it has moved content from several runs into one anchor run. This is the final polishing step for run merging, making the merged run cleaner and safer for later editing.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This simplifies Word tracked-change markup by combining adjacent insertions or deletions from the same author. It turns a row of tiny edit fragments into larger, more understandable edit blocks.

**Data flow**: It receives the path to word/document.xml. It reads the XML while preserving existing blank text, finds paragraph and table-cell containers, and for each one asks _coalesce_in to combine adjacent insertion and deletion elements. If any elements were absorbed, it writes the changed XML back and returns the reduction count.

**Call relations**: unpack_docx calls this before run merging when tracked-change coalescing is enabled. It coordinates the tracked-change cleanup and delegates the detailed work to _coalesce_in for each container and change type.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This looks inside one container for tracked changes of one kind, either insertions or deletions, and groups them by author. It prepares matching edit fragments so they can be merged when they are truly adjacent.

**Data flow**: It receives a container element and a change type string such as 'ins' or 'del'. It finds direct child elements of that type, groups consecutive matching elements that have the same author attribute, passes each group to _merge_change_run, and returns the total number of absorbed change elements.

**Call relations**: _coalesce_tracked_changes calls this for both insertion and deletion markup inside each paragraph or table cell. It uses _merge_change_run to do the actual safe combining once likely candidates have been grouped.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This merges a sequence of tracked-change elements when they are next to each other with only whitespace or comments between them. It keeps separate changes separate when real content appears between them.

**Data flow**: It receives a list of insertion or deletion XML elements, normally from the same author. It keeps the first as the anchor, checks each later element for true adjacency, moves the later element’s children into the anchor when safe, preserves any trailing text, removes the absorbed element from its parent, and returns how many elements were absorbed.

**Call relations**: _coalesce_in calls this for each author group. Before merging any two change elements, it asks _changes_adjacent whether the XML between them is harmless enough to allow the merge.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This answers the safety question for tracked-change merging: are two change elements effectively next to each other? It prevents the script from accidentally merging edits across real intervening content.

**Data flow**: It receives two XML elements. It finds their shared parent, locates their positions among the parent’s children, checks the text and elements between them, and returns true only if the gap contains no meaningful text and no non-comment elements. If the elements are missing from the parent or have no parent, it returns false.

**Call relations**: _merge_change_run calls this before absorbing one tracked-change element into another. It acts as the guardrail that keeps coalescing conservative and avoids changing the document’s meaning.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `document editing / one-off CLI run`

A DOCX file is really a zipped folder of XML files. Adding a comment is not just one edit: Word expects several separate files to agree with each other, like a set of forms that all reference the same ticket number. This file does that bookkeeping for comments.

The script works on an already-unzipped DOCX directory. If the document has no comment files yet, it copies starter XML templates into the word folder and registers those files in the package relationship file and content type file. Those registrations are how Word knows the comment files exist and what they mean.

For each new comment, it makes a random paragraph ID and a durable ID, records the current UTC time, builds the main comment XML with the author, initials, ID, and text, and appends matching entries to Word’s newer comment-support files: extended comment data, comment IDs, and extensible metadata. If the new comment is a threaded reply, it looks up the parent comment’s paragraph ID so Word can connect the reply to the original comment.

One important limitation: this script does not insert the visible comment range into document.xml. It prints the XML markers the caller must add around the text being annotated. Also, the text is expected to already be XML-escaped, so special characters like ampersands must be prepared before calling it.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character uppercase hexadecimal ID, used as a Word paragraph or durable comment identifier. Word uses these IDs to connect related comment records across different XML files.

**Data flow**: It takes no input. It asks for a random number in a safe range, formats that number as eight hexadecimal characters, and returns the resulting string.

**Call relations**: When insert_comment starts adding a comment, it calls this twice: once for the comment paragraph ID and once for the durable ID. Those IDs are then handed to the XML-building helpers so the separate comment files point to the same comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces curly quotation marks and apostrophes with XML entity text. This keeps those characters in a form that Word’s XML can preserve safely.

**Data flow**: It receives a string of XML text. It scans for four curly quote characters and replaces each one with its numeric XML entity, then returns the changed string.

**Call relations**: _serialize_xml calls this just before bytes are written back to disk. It is the final cleanup step in the file-writing path used by _append_element_to_file.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an editable XML tree. Other functions use this whenever they need to inspect or modify one of the DOCX XML files.

**Data flow**: It receives a file path. It reads the file’s raw bytes, parses those bytes with lxml, and returns the root XML element.

**Call relations**: _append_element_to_file uses it before adding a new child element. _ensure_registrations uses it to inspect and update package registration files. _resolve_parent_paragraph uses it to search existing comments for a parent comment.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an edited XML tree back into bytes ready to save. It also applies the curly-quote escaping used by this script.

**Data flow**: It receives an XML root element. It serializes the tree with an XML declaration and UTF-8 encoding, converts the result briefly to text so curly quotes can be replaced, and returns UTF-8 bytes.

**Call relations**: _append_element_to_file calls this after it has added a new XML element. The returned bytes are what get written back to the DOCX XML file.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main Word XML element for the actual comment text. This is the record that contains the comment ID, author, date, initials, and visible comment body.

**Data flow**: It receives the comment ID, author name, initials, timestamp, paragraph ID, and comment text. It creates a Word comment element with a paragraph, a comment-reference marker, and a text run containing the body text, then returns that XML element.

**Call relations**: insert_comment calls this after creating IDs and a timestamp. The returned element is passed to _append_element_to_file so it can be added to comments.xml.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra Word XML record that stores modern comment status and threading information. For replies, this is where the child comment is linked to its parent paragraph.

**Data flow**: It receives the new comment’s paragraph ID and, optionally, the parent comment’s paragraph ID. It creates a commentsExtended.xml element marked as not done, adds the parent reference if there is one, and returns the XML element.

**Call relations**: insert_comment calls this after it has resolved any parent comment. The result is appended to commentsExtended.xml so Word can understand reply relationships and completion status.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML record that ties a comment paragraph ID to a durable ID. A durable ID is a stable identifier Word can use beyond the simple numeric comment ID.

**Data flow**: It receives a paragraph ID and a durable ID. It creates a commentsIds.xml element containing both values and returns it.

**Call relations**: insert_comment calls this for every new comment. The returned element is appended to commentsIds.xml, keeping Word’s ID-tracking file in sync with the main comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the XML metadata record for Word’s extensible comment data. In this file, that metadata stores the durable ID and the UTC date for the comment.

**Data flow**: It receives a durable ID and timestamp. It creates a commentsExtensible.xml element with those attributes and returns it.

**Call relations**: insert_comment calls this after making the durable ID and timestamp. The returned element is appended to commentsExtensible.xml so the newer Word metadata file matches the rest of the comment records.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the paragraph ID for an existing parent comment. This is needed when creating a threaded reply, because Word links replies by paragraph ID rather than only by the visible comment number.

**Data flow**: It receives the path to comments.xml and the numeric parent comment ID. It parses comments.xml, searches for the matching comment, then looks inside it for a paragraph ID and returns that string. If it cannot find one, it returns nothing.

**Call relations**: insert_comment calls this only when the new comment is a reply. Its result is handed to _build_extended_element so the reply can point back to the parent; if no parent paragraph is found, insert_comment reports an error.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to the end of an existing XML file and saves the file. This is the common “open, add, write back” step used for all comment-related files.

**Data flow**: It receives a file path and an XML child element. It parses the file, appends the child to the root element, serializes the updated XML, and writes the bytes back to the same path.

**Call relations**: insert_comment calls this several times: once for the main comment and once for each supporting metadata file. Internally it relies on _parse_xml_file to read the file and _serialize_xml to prepare the updated XML for saving.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure the DOCX package knows about the comment XML files. Without these registrations, Word may ignore the files even if they exist in the folder.

**Data flow**: It receives the unpacked DOCX base directory. It checks document.xml.rels for relationships pointing to the comment files and adds them if missing. It also checks [Content_Types].xml for content type entries and adds those if missing. It writes updated XML files back to disk when it changes them.

**Call relations**: insert_comment calls this when it is adding the first comment and has just copied the comment template files. This function connects those files into the DOCX package structure so Word can discover them.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds one comment or reply to the supporting XML files of an unpacked DOCX document. This is the main function other code or the command-line script uses to perform the comment insertion work.

**Data flow**: It receives the unpacked DOCX path and a CommentSpec containing the comment ID, text, author details, and optional parent ID. It checks for the word folder, creates needed IDs and a timestamp, copies template files if this is the first comment, registers those files, builds the XML records, appends them to the right files, and returns the new paragraph ID plus a success or error message. If a requested parent comment is not found, it returns an error message, though the main comment record has already been appended before that check completes.

**Call relations**: The command-line block builds a CommentSpec from user arguments and calls insert_comment. Inside, this function acts as the coordinator: it calls _make_hex_tag for IDs, the build helpers to create XML elements, _resolve_parent_paragraph for replies, _append_element_to_file to save each record, and _ensure_registrations when the document needs comment files registered for the first time.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`entrypoint` · `document export / command-line packing`

A DOCX file is really a ZIP archive full of XML files and related resources. This script is the “put it back in the box” step after a DOCX has been unpacked and edited as a folder. It checks that the input is a directory and that the output name ends in .docx, then works in a temporary staging area so the original folder is not changed.

Before creating the final ZIP archive, it walks through XML files and relationship files, removing whitespace that is only there for formatting. This is like taking extra blank lines out of a recipe card while carefully leaving the ingredient names untouched. The careful part matters: Word document text lives in special XML tags, and whitespace inside those tags may be meaningful, so the script skips those text-bearing tags.

After cleaning, it creates the output folder if needed, compresses all staged files into a .docx archive, and returns a success or error message. If run directly from the command line, it accepts the input folder and output file path as arguments and exits with an error code if packing failed.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing routine. It takes a folder that represents an unpacked DOCX document, cleans its XML files, and writes a new .docx ZIP archive.

**Data flow**: It receives an input directory path and an output file path. First it checks that the input is really a folder and that the output ends in .docx; if not, it returns no file path and an error message. If the paths are valid, it copies the input folder into a temporary staging folder, cleans each XML and relationship file there, compresses the staged files into the destination DOCX, and returns the output path plus a success message.

**Call relations**: This function is the central workflow. When packing is requested, it creates a temporary workspace, uses shutil.copytree to avoid changing the original files, calls _strip_xml_whitespace on each XML-like file before zipping, and uses zipfile.ZipFile to build the final DOCX archive.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper removes XML whitespace that is only decorative, while preserving whitespace that may be part of the document’s visible text. It helps make the repacked DOCX cleaner without accidentally changing what the user wrote.

**Data flow**: It receives the path to one XML or relationship file. It parses that file into an XML tree, walks through each element, skips known text elements where spaces may matter, removes blank-only text and tail whitespace elsewhere, removes unusual callable-tag children, then writes the XML back to the same file as UTF-8 bytes. If parsing or writing fails, it prints an error to standard error and raises the problem again.

**Call relations**: pack_docx calls this helper for every staged .xml and .rels file before the DOCX archive is created. Internally it relies on lxml.etree.parse to read XML, lxml.etree.tostring to turn the cleaned tree back into bytes, and Path.write_bytes to save the cleaned file.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).


### PowerPoint package maintenance
PowerPoint scripts unpack decks, maintain slide package internals, repair known formatting issues, and repack the result as a valid PPTX file.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`io_transport` · `manual document unpacking / command-line preprocessing`

A .pptx file is really a ZIP archive, which is like a sealed folder containing many smaller files. Most of the important PowerPoint structure inside it is stored as XML, a text format that uses tags to describe slides, relationships, layouts, and settings. This file opens that sealed folder, extracts everything into a normal directory, and then cleans up the XML so a person can read and edit it more safely.

The main workflow starts by checking that the input file exists and ends in .pptx. It then creates the output directory, unzips the PowerPoint contents into it, and finds all XML-like files: normal .xml files and .rels files, which describe relationships between parts of the document. Each of those files is reformatted with consistent indentation, like adding neat spacing to a messy outline. After that, the script replaces curly “smart quotes” with XML entity text, which helps avoid quote characters being misread or damaged during later XML editing.

This file is also usable from the command line. If run directly, it accepts a PowerPoint file path and an output directory, prints either a success or error message, and exits with an error code if unpacking failed.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main unpacking routine. It checks that the given PowerPoint file is usable, extracts its ZIP contents into a folder, and prepares the XML files for human editing.

**Data flow**: It receives a path to a .pptx file and a destination folder. It checks the file, creates the destination folder if needed, unzips the PowerPoint contents there, gathers all .xml and .rels files, prettifies them, escapes curly quotes, and returns either an ExtractionResult with the number of XML files processed plus a message, or no result plus an error message.

**Call relations**: When this script is used, this function is the central step. It calls _prettify_xml first so the extracted XML becomes easier to read, then calls _escape_smart_quotes so special quote characters are written in a safer XML form. It also creates the ExtractionResult that summarizes the work for the caller or command-line output.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper reformats one XML file with clean indentation and a standard XML declaration. It exists so the unpacked PowerPoint internals are practical for a person to inspect and edit.

**Data flow**: It receives the path to one XML-related file. It tries to parse the file as XML, add two-space indentation, convert it back to UTF-8 XML bytes, and overwrite the original file with the cleaner version. If parsing or writing fails, it silently leaves the file as it was.

**Call relations**: extract_pptx calls this helper once for each .xml and .rels file after extraction. It does not decide which files matter; it simply performs the formatting work for each path handed to it.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks with XML entity text. That makes those characters explicit and safer when the XML is later edited or processed.

**Data flow**: It receives the path to one XML-related file, reads it as UTF-8 text, looks for curly single or double quotes, and if any are found, writes the file back with those characters replaced by their XML entity forms. If reading or writing fails, it silently does nothing.

**Call relations**: extract_pptx calls this after prettifying each extracted XML-related file. In the overall flow, it is the final cleanup pass that normalizes quote characters before the unpacked folder is ready for editing.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command invocation`

A PowerPoint file is really a zip file full of XML files, images, charts, themes, and relationship files that say which parts point to which other parts. This script is a small toolbox for working with that structure without opening PowerPoint.

It has three main jobs. The clean command is like tidying a workshop: it looks at the relationship files, finds what is still referenced, removes slides that are no longer listed in the presentation, deletes loose resources such as unused images or charts, and updates the content-type list so the package does not advertise files that are gone. Without this, an edited unpacked presentation can keep stale files that waste space or confuse later tools.

The add command creates a new slide either by copying an existing slide or by making a blank slide attached to a chosen layout. It also registers the new slide in the package metadata and prints the XML line that still needs to be added to the presentation’s slide list.

The thumbnail command makes visual grid images from a .pptx. It asks LibreOffice to render the presentation to PDF, converts the PDF pages to JPEG images, inserts gray placeholders for hidden slides, and lays the results out in one or more labeled contact sheets.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so other code can inspect or edit it. This is the shared doorway into PowerPoint’s XML files.

**Data flow**: It receives a file path → asks lxml to parse the file → returns the root XML element that callers can search or modify.

**Call relations**: The cleaning and slide-adding helpers call this whenever they need to understand a relationship file, the content-type file, or copied slide metadata before making decisions.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to disk with a standard XML declaration and UTF-8 text encoding. It preserves changes made by the script in files PowerPoint expects to read later.

**Data flow**: It receives an XML root element and a destination path → turns the XML tree into bytes → overwrites the destination file with those bytes.

**Call relations**: After helpers remove stale relationships, add new package records, or strip note links, they hand the edited XML tree to this function to save the change.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Finds every file inside the unpacked presentation that is pointed to by a relationship file. These relationship files are the package’s map of what depends on what.

**Data flow**: It receives the unpacked .pptx directory → scans every .rels file → resolves each relationship target to a path inside the package → returns a set of referenced relative paths.

**Call relations**: run_clean calls this during each cleanup pass so it knows which images, charts, themes, notes, and other resources are still in use before deleting anything.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Figures out which slide XML files are actually part of the presentation’s slide order. A slide file can exist on disk but not be listed in the deck.

**Data flow**: It receives the unpacked presentation directory → reads presentation.xml and its relationship file → matches slide relationship IDs to slide file names → returns the names of slides that are active.

**Call relations**: run_clean uses this first, then passes the active slide names to _remove_orphan_slides so unused slide files can be removed safely.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special trash folder used inside the unpacked presentation directory. This clears out files that were already marked as disposable.

**Data flow**: It receives the unpacked directory → looks for the [trash] folder → deletes files inside it and then removes the folder → returns the relative paths it deleted.

**Call relations**: run_clean calls this after removing orphan slides, adding its results to the same deletion report shown to the user.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Removes slide files that are no longer listed as active in the presentation. It also removes their companion relationship files and clears matching references from the presentation relationship file.

**Data flow**: It receives the unpacked directory and the set of active slide names → checks ppt/slides for slide XML files not in that set → deletes those files and their .rels files → updates presentation.xml.rels if needed → returns deleted paths.

**Call relations**: run_clean calls this immediately after identifying active slides. It uses _parse_xml and _write_xml when it needs to edit the presentation’s relationship list.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes supporting files, such as images, charts, diagrams, themes, and notes, when no relationship file points to them anymore. This is the main space-saving cleanup step.

**Data flow**: It receives the unpacked directory and a set of referenced paths → walks known resource folders under ppt → deletes files missing from the referenced set → also removes relationship files whose parent files are gone → returns the deleted relative paths.

**Call relations**: run_clean calls this repeatedly after _collect_all_targets. Repeating matters because deleting one file can make another relationship file irrelevant on the next pass.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes content-type entries for files that were deleted. The content-type file is the package’s table of contents for file kinds, so stale entries can make the package inconsistent.

**Data flow**: It receives the unpacked directory and the list of removed parts → opens [Content_Types].xml → removes Override entries whose PartName matches a deleted file → writes the XML back if anything changed.

**Call relations**: run_clean calls this at the end, after all deletion rounds are finished, so the package metadata matches the files that remain.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint directory. It returns a clear list of everything it removed.

**Data flow**: It receives an unpacked directory → finds active slides → removes orphan slides and trash → repeatedly gathers references and deletes unreferenced resources → updates content types → returns all deleted paths.

**Call relations**: _cmd_clean calls this after checking the directory exists. This function is the coordinator for the clean subcommand and delegates each cleanup step to smaller helpers.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide file number, such as slide7.xml after slide1.xml through slide6.xml. This prevents the script from overwriting an existing slide file.

**Data flow**: It receives the slides directory → scans file names matching slide<number>.xml → finds the largest number → returns one greater, or 1 if there are no slides.

**Call relations**: Both _create_from_layout and _clone_existing call this before creating a new slide file.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to [Content_Types].xml so the PowerPoint package knows the file is a slide. Without this entry, other tools may not recognize the new XML file correctly.

**Data flow**: It receives the unpacked directory and new slide file name → opens the content-type XML → checks whether the slide is already listed → adds an Override entry if missing → writes the file back.

**Call relations**: _create_from_layout and _clone_existing call this after creating or copying the slide file, before the new slide is announced to the presentation relationships.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Creates, or reuses, the presentation-level relationship that points to a slide file. This gives the slide a relationship ID, which is the handle presentation.xml uses to refer to it.

**Data flow**: It receives the unpacked directory and slide file name → opens presentation.xml.rels → looks for an existing relationship to that slide → otherwise picks the next rId number and adds one → writes the file back → returns the relationship ID.

**Call relations**: _create_from_layout and _clone_existing use the returned ID when printing the slide-list XML that the user should add to presentation.xml.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for the presentation’s slide list. This is separate from the relationship ID and is part of PowerPoint’s slide ordering metadata.

**Data flow**: It receives the unpacked directory → reads presentation.xml → extracts existing numeric slide IDs → returns one greater than the largest, or 256 if none are found.

**Call relations**: _create_from_layout and _clone_existing call this after registering the new slide relationship so they can print a complete slide-list entry for the user.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that uses an existing slide layout. This is useful when you want a fresh slide based on a template layout rather than a copy of old content.

**Data flow**: It receives the unpacked directory and layout file name → checks that the layout exists → writes a blank slide XML file and a relationship to the layout → registers the slide in package metadata → prints the new file name and the XML line to add to the slide list.

**Call relations**: run_add calls this when the source name looks like a slideLayout XML file. It relies on the numbering and registration helpers, and exits with an error if the requested layout is missing.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Duplicates an existing slide file and its relationships. It removes any notes-slide relationship from the copy so the duplicated slide does not accidentally share speaker notes.

**Data flow**: It receives the unpacked directory and source slide name → checks the source exists → copies the slide XML to the next slide file name → copies and edits the .rels file if present → registers the new slide → prints the new file name and the XML line to add to the slide list.

**Call relations**: run_add calls this for normal slide sources. It uses _parse_xml and _write_xml only when it needs to remove notes relationships from the copied relationship file.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Decides whether the add command should create a slide from a layout or clone an existing slide. It is the simple dispatcher for slide creation.

**Data flow**: It receives the unpacked directory and a source string → checks whether the source name looks like a slide layout → calls the matching creation helper → produces printed instructions through that helper.

**Call relations**: _cmd_add calls this after validating the directory. It then hands off to either _create_from_layout or _clone_existing.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a .pptx file and determines the presentation’s slide order, including which slides are marked hidden. This keeps thumbnails labeled in the same order a user sees in the deck.

**Data flow**: It receives a .pptx path → opens it as a zip file → reads presentation relationships and presentation.xml → matches relationship IDs to slide file names → returns ordered entries with each slide name and hidden flag.

**Call relations**: run_thumbnail calls this before rendering, so later steps can pair rendered images with the correct slide names and insert placeholders for hidden slides.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the PowerPoint slides into JPEG image files. It does this by using external programs: LibreOffice to make a PDF, then pdftoppm to turn PDF pages into images.

**Data flow**: It receives the .pptx path and a temporary work directory → runs headless LibreOffice to convert the deck to PDF → runs pdftoppm to render JPEG pages → returns the generated image paths in order, or raises an error if conversion fails.

**Call relations**: run_thumbnail calls this inside a temporary directory after extracting slide order. The images it returns are later paired with slide names by _pair_slides_with_images.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a gray crossed-out image used to represent a hidden slide. This lets the thumbnail grid show that a hidden slide exists even if the renderer does not produce a normal image for it.

**Data flow**: It receives image dimensions → creates a gray blank image → draws two diagonal lines across it → returns the placeholder image.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden, then saves the placeholder beside the rendered images.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches slide-order entries to the rendered image files and adds placeholders for hidden slides. This prevents slide labels from drifting out of sync with images.

**Data flow**: It receives the ordered slide list, rendered image paths, and work directory → uses the first rendered image size for hidden placeholders if available → walks the slide list → pairs visible slides with the next rendered image and hidden slides with a generated placeholder → returns image-and-label pairs.

**Call relations**: run_thumbnail calls this after rendering. It calls _make_hidden_placeholder for hidden slides, then hands the finished pairs to _compose_grid.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one contact-sheet image from slide thumbnails and labels. It is like arranging printed photos on a sheet of paper with captions.

**Data flow**: It receives image-and-label pairs, a column count, and a cell width → calculates cell and canvas sizes → draws labels → resizes each slide image to fit → pastes each image into position with a thin outline → returns the final grid image.

**Call relations**: run_thumbnail calls this once for each chunk of slides that fits in a grid. The returned image is then saved as a JPEG file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Runs the full thumbnail-contact-sheet workflow for a PowerPoint file. It returns the paths of the JPEG grid files it creates.

**Data flow**: It receives a .pptx path, output prefix, and column count → reads slide order → creates a temporary workspace → renders slide images → pairs images with labels and hidden placeholders → splits them into grid-sized chunks → saves one or more JPEG grids → returns saved file paths.

**Call relations**: _cmd_thumbnail calls this after checking the input file and limiting the column count. This function coordinates the thumbnail helpers and reports failure by exiting only when no usable slides are found.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line clean subcommand. It checks the user’s path, runs cleanup, and prints a human-readable report.

**Data flow**: It receives parsed command-line arguments → converts the directory argument to a path → exits with an error if it does not exist → calls run_clean → prints either the deleted files or a no-op message.

**Call relations**: build_parser wires this function to the clean subcommand, and the main command-line block calls it through argparse’s selected callback.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line add subcommand. It validates the unpacked presentation directory and starts the slide creation process.

**Data flow**: It receives parsed command-line arguments → converts the directory argument to a path → exits with an error if missing → passes the directory and source name to run_add.

**Call relations**: build_parser assigns this as the callback for the add subcommand, so it runs when the user asks to duplicate a slide or create one from a layout.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line thumbnail subcommand. It checks that the input is a PowerPoint file, runs thumbnail generation, and prints where the output images were saved.

**Data flow**: It receives parsed command-line arguments → validates the .pptx path → caps the requested column count at the allowed maximum → calls run_thumbnail → prints saved grid paths, or prints an error and exits if something fails.

**Call relations**: build_parser wires this to the thumbnail subcommand. It is the user-facing wrapper around the rendering and grid-making workflow.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface: the available subcommands, their arguments, help text, and which function each command should run.

**Data flow**: It creates an argparse parser → adds clean, add, and thumbnail subcommands with their expected arguments → attaches each subcommand to its callback function → returns the ready parser.

**Call relations**: The script’s main block calls this when slides.py is run directly. The parser then reads the user’s command and dispatches to _cmd_clean, _cmd_add, or _cmd_thumbnail.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`util` · `post-generation cleanup`

A .pptx file is really a ZIP archive full of XML files. This script opens that archive, checks for a few known problems, and rewrites the archive only if something needs fixing. The first problem is “phantom” slide master records: the file list says a slide master exists, but the actual slide master XML file is missing. PowerPoint may complain that the presentation is damaged. The second problem is ZIP directory entries, which are normal in many ZIP files but not valid for this kind of Office package. The third problem is subtle: text XML elements that start or end with spaces or tabs need an `xml:space="preserve"` marker, or PowerPoint may remove those spaces. That can break indented code, aligned text, or intentional padding. The script reads all real files from the archive, detects these issues, prepares corrected XML where needed, then writes a temporary .pptx without directory entries and with corrected content. If the rewrite succeeds, it replaces the original file. Used after presentation generation, this acts like a cleanup pass before handing the file to PowerPoint.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function finds PowerPoint text elements whose text begins or ends with a space or tab, then marks them so PowerPoint keeps that whitespace. It protects content such as indented code blocks or carefully aligned text from being changed when the presentation is opened.

**Data flow**: It receives a dictionary of PPTX archive entries, where each entry name points to its raw file bytes. It looks only at XML files that can contain slide, layout, master, or notes text, parses them as XML, and inspects each DrawingML text element. When it finds leading or trailing spaces or tabs without the preserve marker, it adds that marker and stores the rewritten XML bytes. It returns two things: a dictionary of only the files that were changed, and a count of how many text elements were fixed.

**Call relations**: The main repair flow calls this after it has read the PPTX archive into memory. This helper does the focused text-spacing work, using XML parsing and XML writing, then hands the changed files back to `repair` so they can be written into the repaired presentation archive.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a PPTX file. It checks whether the file exists, looks for known PowerPoint compatibility problems, and if needed rewrites the presentation safely through a temporary file.

**Data flow**: It receives a filename and turns it into a filesystem path. It opens the .pptx as a ZIP archive, reads all non-directory entries, records which slide master files really exist, and checks `[Content_Types].xml` for slide master records that point to missing files. It also asks `_repair_whitespace_preservation` to prepare text-spacing fixes. If nothing is wrong, it prints that no repairs are needed and returns `True`. If repairs are needed, it writes a new temporary ZIP archive, skipping invalid directory entries and replacing damaged XML with corrected XML. Finally it moves the temporary file over the original file, prints how many fixes were applied, and returns `True`; if the input file is missing, it prints an error and returns `False`.

**Call relations**: This function is called by the command-line block when someone runs the script directly with a PPTX filename. During its work it relies on ZIP file reading and writing, regular expression checks for Office XML paths and content records, and the whitespace helper for text-specific fixes. It is the coordinator that brings all repair checks together and decides whether to leave the file alone or replace it with a cleaned version.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`entrypoint` · `manual packaging or command-line run after PPTX contents have been edited`

A .pptx file is really a ZIP archive full of XML files and other resources. This file is the “put it back in the box” tool: it takes a directory that contains the unpacked pieces of a PowerPoint file, cleans the XML, and compresses everything into a new .pptx archive. Without this step, edited PowerPoint contents would remain as loose folders and files instead of a presentation PowerPoint can open.

The main work starts by checking two simple rules: the source must be a directory, and the destination must end in .pptx. It then copies the source into a temporary work area, so the original folder is not changed. Next, it walks through XML files and relationship files, which are the files PowerPoint uses to describe slides, layouts, images, and links between parts. Each one is passed through a cleanup step that removes whitespace used only for formatting the XML, like indentation and blank gaps.

One important detail is that DrawingML text nodes are protected. DrawingML is the XML language Office uses for slide text and shapes. The script avoids stripping whitespace from text-bearing tags, because a space in a slide title or bullet point may be real content, not decoration. Finally, it writes all files into a compressed ZIP archive with the .pptx name.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a PowerPoint file from an unpacked folder. It is the main reusable operation behind the script: check the inputs, clean the XML files, and write the final .pptx archive.

**Data flow**: It receives a source directory path and an output file path. It first turns them into Path objects, verifies that the source is a real directory and the output name ends in .pptx, then copies the whole source into a temporary folder. In that temporary copy, it finds every .xml and .rels file and sends each one to _condense_xml. After cleanup, it creates the output folder if needed, writes all files into a compressed ZIP archive, and returns the output path plus a success message. If the inputs are wrong, it returns no path and an error message instead.

**Call relations**: This is the top-level packing flow used by the command-line part of the script. During that flow, it calls _condense_xml for each XML-like file so the archive is compact and safe for PowerPoint. It also relies on standard file tools for copying the folder, making a temporary workspace, and creating the ZIP file.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that exists only to make the XML readable to humans. It deliberately keeps whitespace inside text nodes so slide text is not accidentally changed.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through every node, skips protected text-style tags, removes blank-only text before or after elements, and removes unusual parser nodes whose tag behaves like a callable object. It then writes the cleaned XML back to the same file using a UTF-8 XML declaration. If parsing or writing fails, it prints an error message to standard error and raises the problem again so the caller knows packing failed.

**Call relations**: assemble_pptx calls this helper once for each .xml and .rels file in the temporary copy of the presentation contents. After _condense_xml finishes a file, assemble_pptx later includes that cleaned file in the final .pptx ZIP archive.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### Spreadsheet recalculation
LibreOffice transport helpers support recalculating Excel workbooks and reporting remaining formula errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`io_transport` · `document processing`

This file is a thin wrapper around LibreOffice’s command-line program, `soffice`. The larger Excel-document tooling can use it when it needs LibreOffice to open, convert, or process spreadsheet files in the background. Without these helpers, each script would have to remember the right environment setting, command shape, and platform-specific macro folder path on its own.

The key idea is to make LibreOffice behave like a quiet background worker. `soffice_env` copies the current process environment and adds `SAL_USE_VCLPLUGIN=svp`, which tells LibreOffice to use a headless-style display backend instead of trying to show normal desktop windows. Think of it like asking a printer to do the job silently in the back room rather than walking to the front desk.

`macro_dir` picks the usual LibreOffice macro folder for the current operating system. macOS and Linux store these files in different places, so this helper hides that difference.

`run_soffice` builds the actual `soffice` command, runs it through Python’s subprocess system, captures its text output and error output, and optionally stops it if it takes too long. This gives the rest of the project one simple way to call LibreOffice safely and consistently.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when launching LibreOffice. Its main job is to make LibreOffice run with a quiet, non-windowed display backend suitable for automated scripts.

**Data flow**: It starts with a copy of the current process environment, so existing settings are preserved. It then adds or replaces the `SAL_USE_VCLPLUGIN` setting with `svp`. The result is a dictionary of environment variables that can be passed to a new LibreOffice process.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls `soffice_env` to get the right environment. This keeps the launch behavior consistent for every LibreOffice command run through this helper.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Returns the folder where LibreOffice stores user macros for the current operating system. Scripts can use this when they need to install, find, or work with LibreOffice macro files.

**Data flow**: It checks the operating system name, chooses the matching macro-folder template for macOS or Linux, expands the `~` home-directory shortcut into a real path, and returns it as a `Path` object. If the operating system is not recognized, it falls back to the Linux-style location.

**Call relations**: This helper stands on its own for code that needs the LibreOffice macro directory. Internally, it asks the platform library what system it is running on and hands the final folder string to `Path` so callers receive a path object instead of plain text.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program with the given arguments and returns the finished process result. It is the main helper other scripts use when they need LibreOffice to do work in the background.

**Data flow**: It receives a list of LibreOffice command arguments and an optional timeout. It prefixes those arguments with the `soffice` program name, prepares the special LibreOffice environment with `soffice_env`, then starts the external process while capturing its standard output and error text. It returns Python’s completed-process object, which includes the exit code and captured text.

**Call relations**: This function is the outward-facing launcher in the file. Before handing control to Python’s subprocess runner, it calls `soffice_env` so LibreOffice starts with the expected headless-friendly settings. The subprocess system then runs the actual external `soffice` program and reports back what happened.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on-demand spreadsheet recalculation`

Excel files can contain formulas whose saved results are stale or wrong until a spreadsheet program recalculates them. This file solves that by using LibreOffice in “headless” mode, meaning LibreOffice runs in the background without showing a window. It installs a small LibreOffice macro, like a tiny script inside LibreOffice, that tells the open spreadsheet to calculate every formula, save itself, and close.

Before running LibreOffice, the script takes a snapshot of table style information inside the `.xlsx` file. An Excel file is really a zip archive full of XML files, and LibreOffice can sometimes rewrite table formatting in a way this project wants to avoid. After recalculation, the script patches those table style pieces back in.

Then it opens the saved workbook with `openpyxl`, a Python library for reading Excel files, and searches cell values for common Excel error strings such as `#REF!`, `#DIV/0!`, and `#VALUE!`. It also counts how many formulas the workbook contains. The final result is a JSON-friendly summary: success, errors found, or a clear failure message if LibreOffice could not run, timed out, or the file could not be read.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the small macro needed to recalculate and save the spreadsheet. Without this macro, the script could launch LibreOffice but would not have a reliable command to tell it, “calculate everything, save, and close.”

**Data flow**: It asks `_soffice.macro_dir` where LibreOffice user macros live, then checks whether `Module1.xba` already contains the needed `RecalculateAndSave` macro. If the macro folder is missing, it briefly starts LibreOffice headlessly to create the expected user profile area, then writes the macro file. It returns `true` if the macro is ready and `false` if writing it failed.

**Call relations**: The main `recalc` flow calls this before touching the spreadsheet. It relies on `_soffice.macro_dir` for the macro location, `_soffice.soffice_env` for the right LibreOffice environment, and `subprocess.run` to initialize LibreOffice when needed.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: Copies the existing table style snippets from inside the Excel file before LibreOffice rewrites the file. This protects formatting details that might otherwise be lost or changed during recalculation.

**Data flow**: It receives the path to an `.xlsx` file, opens it as a zip archive, and looks through XML files under `xl/tables/`. For each table XML file, it searches for a `<tableStyleInfo .../>` element and stores that raw XML bytestring in a dictionary keyed by the file name inside the archive. It returns that dictionary of saved style snippets.

**Call relations**: `recalc` calls this just before launching LibreOffice. Later, `recalc` passes the saved style snippets to `_restore_table_styles` so the formatting can be put back after recalculation.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: Puts one saved table style snippet back into a table XML file. It is the small repair step used while rebuilding the spreadsheet archive.

**Data flow**: It receives the raw XML bytes for one table file and the saved table style XML bytes. If the table already has a table style element, it replaces that element. If not, it inserts the saved style just before the closing `</table>` tag. It returns the patched XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file that had a saved style. It does not read or write files by itself; it only transforms one piece of XML data.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: Restores table formatting snippets that were saved before LibreOffice recalculated the workbook. This reduces unwanted formatting changes caused by rewriting the `.xlsx` file.

**Data flow**: It receives the spreadsheet path and a dictionary of saved table style XML snippets. If there are no saved styles, it does nothing. Otherwise, it opens the original `.xlsx` zip archive, writes a temporary replacement archive, patches any matching table XML files with `_patch_table_style`, and then moves the temporary file over the original. If something goes wrong, it removes the temporary file if it exists.

**Call relations**: `recalc` calls this after LibreOffice successfully recalculates and saves the spreadsheet. It uses `_patch_table_style` for the XML repair, `zipfile.ZipFile` to read and write the workbook archive, `shutil.move` to replace the file, and `os.remove` only for cleanup after a failed attempt.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: Looks through the recalculated workbook for visible Excel error values. This tells the caller whether formulas still produced problems after recalculation.

**Data flow**: It receives the path to the workbook and opens it with `openpyxl.load_workbook` using `data_only=True`, which means it reads the stored calculated results rather than the formula text. It walks every worksheet, row, and cell. When a cell contains a string with an Excel error such as `#REF!` or `#DIV/0!`, it records the worksheet name and cell coordinate. It closes the workbook and returns a dictionary mapping each error type to the places where it appeared.

**Call relations**: `recalc` calls this after LibreOffice has saved the recalculated file and after table styles have been restored. The returned error map becomes the basis of the final JSON summary.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: Counts how many formula cells are in the workbook. This gives useful context in the final report, such as whether a file had hundreds of formulas or none at all.

**Data flow**: It receives the workbook path and opens it with `openpyxl.load_workbook` using `data_only=False`, which means it reads formula text instead of only saved results. It walks every cell in every worksheet and increments a counter whenever a string value starts with `=`. It closes the workbook and returns the count.

**Call relations**: `recalc` calls this near the end, after scanning for errors. Its result is included in the final response alongside the error totals.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: Runs the full recalculation-and-check process for one Excel file. This is the main reusable function: give it a filename and optional timeout, and it returns a plain dictionary describing success, spreadsheet errors, or a failure reason.

**Data flow**: It receives a filename and timeout. First it checks that the file exists. Then it makes sure the LibreOffice macro is installed, saves table style snippets, and runs LibreOffice headlessly with the macro command against the absolute file path. If LibreOffice times out or exits with an error, it returns an error dictionary. If recalculation succeeds, it restores table styles, scans for Excel error values, counts formulas, and returns a summary with status, total error count, total formula count, and limited cell locations for each error type.

**Call relations**: `main` calls this when the script is run from the command line. Inside, it coordinates all helper functions: `_ensure_macro` prepares LibreOffice, `_snapshot_table_styles` and `_restore_table_styles` protect formatting, `_scan_errors` checks results, `_count_formulas` adds context, and `_soffice.run_soffice` performs the actual background LibreOffice run.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for the script. It lets someone run the recalculation check from a terminal and receive a JSON report.

**Data flow**: It reads command-line arguments from `sys.argv`. If no spreadsheet path is provided, it prints usage instructions and exits with an error code. Otherwise, it reads the filename, optionally reads a timeout value, calls `recalc`, converts the returned dictionary to formatted JSON with `json.dumps`, and prints it.

**Call relations**: This is called only when the file is executed directly as a script. It is the outer shell around `recalc`: it translates terminal input into function arguments and turns the function result into printed JSON.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).


### PDF form and rendering tools
PDF utilities inspect and fill native forms, mark up non-fillable layouts, and render pages to images.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `on-demand command-line use for PDF form inspection and filling`

PDF forms can store fillable boxes, checkboxes, radio buttons, and dropdowns inside the PDF itself. This file gives the project a way to read and write those built-in fields instead of guessing where text should be drawn on the page. Without it, automated form filling would either fail on real PDF forms or fall back to manual coordinate-based placement.

The file uses pypdf, a Python library for reading and writing PDFs. First, it can check whether a PDF has form fields. Then it can extract the fields into simple JSON: each field gets a name, type, page number, and rectangle location. The rectangle coordinates are converted into a more screen-like top-down system, because PDFs normally measure from the bottom of the page, which is unintuitive for humans.

The code understands several field types: plain text fields, checkboxes, radio button groups, and choice fields such as dropdowns. It also copes with two PDF layouts: normal AcroForm metadata, and “orphaned widgets,” where the clickable form pieces are attached directly to pages. Finally, the fill command validates requested values before writing, so a checkbox, radio group, or dropdown is not given an impossible value.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF contains fillable widgets attached directly to pages instead of listed in the main form table. This matters because some PDFs are valid enough to fill but do not report fields through the usual PDF form index.

**Data flow**: It takes a PdfReader, looks through each page’s annotations, and searches for widget annotations that also declare a field type. It returns true as soon as it finds one; otherwise it returns false after checking the whole document.

**Call relations**: The detect command calls this after checking the normal form fields. It is the fallback test that lets cmd_detect say a PDF is fillable even when the standard field lookup misses these page-level widgets.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a field annotation by walking up through its parent fields. This is needed because PDFs can store field names like nested folders, where the visible widget only knows part of its name.

**Data flow**: It starts with one annotation dictionary, collects each /T name it finds while moving through /Parent links, reverses those pieces, and joins them with dots. If it finds no name pieces, it returns nothing.

**Call relations**: The AcroForm extraction path calls this while scanning page annotations. Its result lets _extract_from_acroform match a page widget back to the field metadata found earlier.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns a raw PDF field dictionary into one of this file’s simpler field objects. It hides PDF-specific codes behind everyday categories such as text, checkbox, choice, or unknown.

**Data flow**: It receives the raw PDF field data and a field name, reads the PDF field type code, and chooses the right local data object. Text fields become plain FormField objects, button fields are delegated to checkbox-building logic, choice fields are delegated to choice-building logic, and unfamiliar types are kept as unknown fields.

**Call relations**: Both extraction paths use this as their translator from PDF internals to the project’s simpler field model. When the field is a button or choice, it hands off to _build_checkbox or _build_choice for the details.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox description and tries to identify which stored value means checked and which means unchecked. This is important because PDFs do not always use a simple true/false value for checkboxes.

**Data flow**: It reads the checkbox’s possible states from the raw PDF data. If it sees the common two-state pattern with /Off, it picks the other value as the checked value. If the states look unusual, it warns the user and still records both values as best it can. It returns a CheckboxField.

**Call relations**: _build_field_from_dict calls this whenever it sees a PDF button field. The resulting CheckboxField is later used during extraction output and during fill-value validation.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a dropdown or list-style choice field. It records both the value the PDF expects and the human-readable text when both are available.

**Data flow**: It reads the raw choice states from the PDF. Each option is converted into a small dictionary with a value and display text, using the same text for both if the PDF only provides one item. It returns a ChoiceField.

**Call relations**: _build_field_from_dict calls this when it sees a PDF choice field. The choices it records are later written to extracted JSON and used to reject invalid fill values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a missing checkbox checked value by looking at the checkbox’s appearance data. This helps with PDFs where the normal field metadata does not clearly say what value marks the box as checked.

**Data flow**: It receives a resolved annotation and an existing CheckboxField. If the checkbox already has an on value, it does nothing. Otherwise it looks inside the annotation’s normal appearance states, chooses the first state that is not /Off, and writes that into the CheckboxField.

**Call relations**: _extract_from_widgets calls this after building a checkbox from a page-level widget. It improves the field information before the widget is returned to the rest of the extraction flow.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from bottom-up page coordinates into top-down coordinates that are easier for people and many screen tools to understand. It is like translating a map drawn from the floor upward into one drawn from the ceiling downward.

**Data flow**: It receives a rectangle as left, bottom, right, top plus the page height. It converts the vertical numbers by subtracting them from the page height, then returns a new rectangle as left, top, right, bottom.

**Call relations**: The widget extractor, AcroForm extractor, and radio option collector all use this before recording field locations. That keeps all exported rectangle data in the same coordinate style.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts form fields by scanning the annotations on each page directly. This is the fallback route for PDFs whose form fields are not available through the normal AcroForm field list.

**Data flow**: It takes a PdfReader, loops over pages and their annotations, keeps only widget annotations with field types, builds a simpler field object for each one, adds the page number, converts the rectangle, improves checkbox values when needed, and returns the list of fields.

**Call relations**: _extract_from_acroform calls this when the standard field lookup finds nothing. Inside, it relies on _build_field_from_dict, _flip_rect, and _extract_checkbox_on_value to turn raw page widgets into useful field descriptions.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the main field list from a PDF’s AcroForm data, which is the standard place where fillable PDF fields are described. It also connects those fields to their page locations.

**Data flow**: It asks the PdfReader for raw form fields. If none are found, it falls back to scanning widgets directly. Otherwise it builds field objects by name, notes possible radio button groups, scans page annotations to find where each field appears, gathers radio options, warns about fields it cannot locate, sorts the final list, and returns it.

**Call relations**: cmd_extract and cmd_fill both call this as their main way to understand a PDF form. It coordinates several helpers: _build_field_from_dict for field type translation, _full_field_name for matching widgets to names, _flip_rect for coordinates, _collect_radio_option for radio buttons, and _extract_from_widgets as the fallback.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one clickable radio-button option to a radio group description. Radio groups are special because several separate widgets share one field name but each has a different selectable value.

**Data flow**: It receives one annotation, the group name, page information, page height, and the growing radio-group dictionary. It finds the one non-/Off appearance key, creates the group if needed, converts the option rectangle, and appends the option value and location to the group.

**Call relations**: _extract_from_acroform calls this while scanning page annotations for fields that looked like radio groups. It uses _flip_rect so radio option locations match the rest of the extracted field data.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a consistent ordering for extracted fields: page first, then rough row, then left-to-right position. This makes the JSON easier for humans to read because fields appear in page order rather than random PDF storage order.

**Data flow**: It receives a field. For radio groups it uses the first option’s rectangle; for other fields it uses the field rectangle. It rounds the vertical position into coarse rows and returns a tuple that can be used for sorting.

**Call relations**: This function is used as the sorting rule when extracted AcroForm fields are combined. It does not hand work off to other helpers; it simply supplies the ordering recipe.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts an internal field object into plain JSON-friendly data. This is the final cleanup step before writing extracted field information to a file.

**Data flow**: It receives a FormField or one of its specialized forms. It always writes the name and kind, adds page and rectangle when present, and adds checkbox values, radio options, or choice options depending on the field type. It returns a normal dictionary.

**Call relations**: cmd_extract calls this for every extracted field before writing JSON. It is the bridge between the dataclass objects used inside the script and the simple list of dictionaries users see in the output file.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a proposed value is allowed for a checkbox, radio group, or choice field. This prevents writing values that the PDF form does not recognize.

**Data flow**: It receives a field description and the requested value. For checkboxes it compares the value with the recorded on and off values. For radio groups and choice fields it compares against the available option values. It returns an error message if the value is invalid, or nothing if it is acceptable.

**Call relations**: _validate_fill_entries calls this while reviewing each requested fill entry. The result decides whether cmd_fill can continue or must stop before writing the output PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the detect subcommand, which tells the user whether a PDF appears to contain native fillable fields. It is a quick yes/no check before trying extraction or filling.

**Data flow**: It receives command-line arguments, expects exactly one PDF path, opens that PDF with PdfReader, checks the normal form fields and then orphaned widgets, and prints a message saying whether fillable fields were found. If the arguments are wrong, it prints usage text and exits with an error.

**Call relations**: main dispatches to this when the user runs the detect command. It calls _has_orphaned_widgets only if the normal PDF field check is not enough to prove the form is fillable.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract subcommand, which writes a JSON map of all detected PDF form fields. Users can inspect or edit this JSON before preparing fill values.

**Data flow**: It receives command-line arguments, expects an input PDF and output JSON path, opens the PDF, extracts fields, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written. If the arguments are wrong, it prints usage text and exits.

**Call relations**: main dispatches to this for the extract command. It relies on _extract_from_acroform to understand the PDF and _field_to_dict to turn the result into JSON-safe data.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill subcommand, which creates a new PDF with form values filled in from a JSON file. It validates the requested entries first so bad field names or impossible values do not silently produce a broken form.

**Data flow**: It receives command-line arguments, expects an input PDF, a values JSON file, and an output PDF path. It reads the values, extracts the PDF’s field metadata, validates names, pages, and allowed values, groups fill values by page, clones the PDF into a writer, updates each page’s form fields, writes the output PDF, and prints a summary. If validation fails, it exits before writing.

**Call relations**: main dispatches to this for the fill command. It calls _extract_from_acroform to learn the form structure and _validate_fill_entries to decide whether the requested fill data is safe to apply before using pypdf’s writer.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks a whole list of requested fill entries against the fields actually present in the PDF. It catches wrong names, wrong pages, and invalid field values before any PDF is written.

**Data flow**: It receives the user’s value entries and a lookup table of known fields by name. For each entry it checks that the field exists, that the page matches if supplied, and that the value is valid when present. It prints any errors it finds and returns true if there was at least one error, otherwise false.

**Call relations**: cmd_fill calls this after reading the values JSON and extracting PDF metadata. For value-specific checks, it delegates to _validate_fill_value.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Routes the command-line invocation to the requested subcommand. It is the script’s front door when this file is run directly.

**Data flow**: It reads sys.argv, checks that the user supplied a known command name, prints overall usage and exits if not, and otherwise calls the matching command function with the remaining arguments.

**Call relations**: The Python runtime calls main when the file is executed as a script. main then dispatches to one of the registered subcommand functions: detect, extract, or fill.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line run: extract, preview, or fill`

Many PDFs look like forms but do not contain real fillable fields. This file helps bridge that gap. First, it can inspect a PDF page and collect landmarks: visible words, long horizontal lines, small square boxes that look like checkboxes, and row areas between lines. That output can be saved as JSON so another person or tool can decide where text should go. Second, it can preview a field plan by drawing red and blue rectangles on an image of a page, like laying transparent sticky notes over a paper form. Third, it can fill the PDF by adding FreeText annotations, which are pieces of visible text placed on top of the page without changing the original PDF artwork.

A key detail is that PDFs and images use different coordinate systems. Images usually count downward from the top-left corner, while PDF annotations are positioned from the bottom-left. The CoordMapper class converts between these systems so text lands in the intended box. Before writing the output PDF, the fill step checks for common mistakes: boxes that are too short for the font size, and rectangles that overlap on the same page. If it finds errors, it prints them and stops rather than creating a confusing PDF.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: This method converts a rectangle from the field description into the coordinate style needed by PDF annotations. It matters because the same visual box can have different numbers depending on whether it came from a PDF page or an image preview.

**Data flow**: It receives a bounding box as four numbers. If the source coordinates came from an image, it scales them to the PDF page size and flips the vertical direction; otherwise it only flips the vertical direction from top-based page coordinates into bottom-based PDF coordinates. It returns a four-number rectangle ready to give to the PDF annotation library.

**Call relations**: During filling, _validate_and_fill creates a CoordMapper for each field’s page and asks this method to translate the field’s content area before creating the visible text annotation.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: This function reads one PDF page and pulls out simple layout clues that help identify where a non-fillable form might have fields. It looks for words, long horizontal lines, and small square boxes that are likely checkboxes.

**Data flow**: It receives a pdfplumber page object and a page number. It creates a PageLayout record, scans the page’s line objects for long horizontal rules, scans rectangle objects for small near-square tick boxes, and asks the page to extract its words. It returns a PageLayout filled with those findings and rounded coordinates.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. After _extract_page builds the raw page layout, _extract_all_pages passes that layout to _compute_row_ranges to add row information.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: This function turns detected horizontal lines into row bands. It helps later readers see the spaces between lines, which often correspond to rows in a form or table.

**Data flow**: It receives a PageLayout that already has horizontal rule positions. It sorts the vertical positions of those rules, then creates one row range for each neighboring pair of lines. It changes the PageLayout in place by adding top, bottom, and height values to its row_ranges list.

**Call relations**: _extract_all_pages calls this right after _extract_page. It enriches each page’s extracted layout before the page is saved into the final extraction result.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: This function scans an entire PDF, page by page, and builds a list of layout summaries. It is the main worker behind the extract command.

**Data flow**: It receives a path to a PDF file. It opens the PDF with pdfplumber, loops through every page, extracts that page’s layout, computes its row ranges, and stores the result. It returns a list of PageLayout objects, one for each page.

**Call relations**: cmd_extract calls this after checking the command-line arguments. Inside the loop, it delegates the detailed page reading to _extract_page and the row calculation to _compute_row_ranges.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: This function converts PageLayout objects into plain dictionaries that can be written as JSON. It is a translation step from Python objects into a portable data format.

**Data flow**: It receives a list of PageLayout records. For each page, it copies the page number, dimensions, text elements, horizontal rules, tick boxes, and row ranges into a dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after _extract_all_pages finishes. The command then serializes the returned dictionaries to JSON so the scan results can be saved and used outside this Python process.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: This function adds text to a non-fillable PDF according to a JSON field plan, but only after checking that the plan is safe enough to use. It is the main worker behind the fill command.

**Data flow**: It receives an input PDF path, a JSON fields path, and an output PDF path. It reads the field plan, opens the PDF, records each page’s size, then walks through the requested form fields. For each field with text, it checks whether the content box is tall enough for the chosen font and whether it overlaps earlier content or label boxes on the same page. If errors are found, it prints them and exits. If the plan is acceptable, it converts the content box into PDF annotation coordinates, creates a FreeText annotation, adds it to the correct page, and finally writes the new PDF file.

**Call relations**: cmd_fill calls this after validating the number of command-line arguments. Inside the fill process, it uses _rects_overlap for collision checks and CoordMapper.to_annotation_rect to place each annotation in the PDF’s coordinate system.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: This small helper answers one question: do two rectangles overlap? It is used to catch field layouts where text or labels would collide.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges and returns true if they occupy any shared area, or false if one is completely to the side or above the other.

**Call relations**: _validate_and_fill calls this while checking each new field against rectangles already placed on the same page. Its result becomes either a validation error or permission to continue.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: This is the command handler for scanning a PDF and saving its detected layout as JSON. A user would run it when they need a map of the PDF before deciding where form text should go.

**Data flow**: It receives the command arguments after the word extract. It expects an input PDF path and an output JSON path; if they are missing, it prints usage help and exits. Otherwise it scans all pages, converts the results to dictionaries, writes pretty-printed JSON, and prints a short summary of how many words, lines, tick boxes, and row ranges were found.

**Call relations**: main dispatches to this command when the user chooses extract. The command relies on _extract_all_pages for the actual PDF reading and _pages_to_dict for turning the results into JSON-ready data.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: This is the command handler for drawing a visual preview of planned form fields on a page image. It helps a human quickly check whether the rectangles in the fields JSON line up with the form.

**Data flow**: It receives a page number, a fields JSON file, an input image path, and an output image path. It opens the JSON and image, then draws red rectangles around content areas and blue rectangles around label boxes for fields on the chosen page. It saves the marked-up image and prints how many fields were highlighted.

**Call relations**: main dispatches to this command when the user chooses preview. Unlike the fill path, this does not change a PDF; it uses the same field rectangle data only to create a human-friendly image check.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: This is the command handler for creating a new PDF with text annotations placed into planned fields. It is the user-facing doorway to the fill behavior.

**Data flow**: It receives the command arguments after the word fill. It expects an input PDF, a fields JSON file, and an output PDF; if the count is wrong, it prints usage help and exits. With valid arguments, it passes the three paths to _validate_and_fill.

**Call relations**: main dispatches to this command when the user chooses fill. The command itself stays thin and hands all validation and PDF-writing work to _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: This is the script’s starting point when run from the command line. It chooses which subcommand to run: extract, preview, or fill.

**Data flow**: It reads the command-line arguments from sys.argv. If the user did not provide a known subcommand, it prints a compact usage message and exits. Otherwise it looks up the selected command and passes along the remaining arguments.

**Call relations**: When this file is executed directly, Python calls main through the usual __main__ guard. main is the top-level dispatcher that connects the user’s command-line choice to cmd_extract, cmd_preview, or cmd_fill.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command-line PDF rendering`

This script solves a practical problem: many document tools need page images, not just the original PDF file. A PDF is like a bound booklet, while this file tears it into separate picture pages that other parts of a system, or a human, can inspect more easily.

The main work happens in `render`. It creates the output folder if it does not already exist. Then it asks `pdf2image` to open the PDF and convert each page into an image at 200 DPI, meaning a reasonably detailed image resolution. For each page image, it checks whether the width or height is bigger than 1000 pixels. If so, it shrinks the image while keeping the same shape, so pages do not become too large to store or view comfortably. Each page is saved as `page_1.png`, `page_2.png`, and so on.

The file also has a small command-line wrapper, `main`. It checks that the user supplied exactly two arguments: the PDF to read and the folder to write into. If the arguments are wrong, it prints a usage hint and stops. Without this file, there would be no simple built-in way here to render PDF pages into image files for later processing or inspection.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts every page of a PDF into a PNG image and writes those images into a chosen folder. It also keeps images from becoming too large by shrinking any page image whose width or height is over 1000 pixels.

**Data flow**: It receives a PDF file path and an output folder path. It creates the folder if needed, reads the PDF through `pdf2image.convert_from_path`, resizes oversized page images, saves each page as a numbered PNG file, and prints progress messages showing where each image went. Its output is a set of PNG files on disk; it does not return a value.

**Call relations**: This is the worker function used by `main` after the command-line arguments have been checked. It hands the actual PDF reading to the external `pdf2image` library and uses `pathlib.Path` to create and build file paths safely.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the script. It makes sure the user gave the script the two required pieces of information: the source PDF and the destination folder.

**Data flow**: It reads the command-line arguments from `sys.argv`. If the argument count is wrong, it prints an example of the correct command and exits with an error code. If the arguments are correct, it passes them to `render`, which creates the image files.

**Call relations**: This function is called when the file is run as a script. Its job is to guard the front door: check the user input first, then call `render` to do the conversion work; if the input is incomplete, it calls `sys.exit` to stop early.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).

## 📊 State Registers Touched

- `reg-document-review-state` — The saved document-review progress and issue log files that document tools write once and later annotation/packing tools read to modify PDFs, slides, spreadsheets, or Word files.
