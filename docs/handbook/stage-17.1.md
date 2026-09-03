# Document review state and annotation scripts  `stage-17.1`

This stage is the document-review skill’s backstage workbench. It does not review the document by itself. Instead, it stores what the review has found and turns those findings into comments that people can see in the final files.

The small __init__.py file simply makes the scripts folder importable by Python. constants.py keeps the agreed file names for the saved review state and the review log, so every script looks in the same place. models.py defines what a review issue looks like, such as the problem found and where it belongs, and can format an issue as a readable comment.

manage_state.py is the record keeper. It updates a JSON state file, which is a plain text data file, with sections, claims, issues, progress, summaries, and an audit trail. The annotation scripts then use that saved state. annotate_pdf.py highlights matching PDF text and adds notes. annotate_pptx.py writes findings as PowerPoint comments. annotate_xlsx.py copies a spreadsheet and adds comments to cells. Together, they turn review data into visible feedback.

## Files in this stage

### Review state foundations
Package markers, shared constants, issue models, and the state-management CLI define the document-review data that later annotation scripts consume.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package, rather than just an ordinary directory. That matters because code elsewhere may need to refer to scripts inside this folder using Python import paths. Think of it like putting a label on a drawer: the drawer may hold the useful tools, but the label tells the system where the drawer is and lets it be opened by name. Since this file contains no code, it does not run any setup, change any settings, or expose any functions. Its value is structural: without it, depending on the Python version and import style, modules in this directory might be harder or impossible to import reliably.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a tiny configuration-style file. It does not run any steps by itself. Instead, it acts like a label maker for the document review workflow. The workflow needs to remember its current progress, and it also needs to record a line-by-line history of what happened during review. This file names those two storage files: `document_review_state.json` for the saved state, and `review_log.jsonl` for the log.

The important idea is consistency. If different parts of the document review code typed these filenames by hand, one spelling mistake could make the system write to one file and read from another. By putting the names here, other files can import the constants and use the same values everywhere.

The `.json` state file is meant to hold structured saved data, such as where the review left off. The `.jsonl` log file means “JSON Lines,” where each line is a separate JSON record, which is useful for appending events over time. Without this file, the filenames would likely be scattered through the code, making changes harder and mistakes easier.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review comment creation`

This file is like the blank form used by a document reviewer. The `DocumentIssue` type says which fields every review issue is expected to have, such as its type, severity, description, location, original text, suggested replacement, and links to related issues. That matters because other parts of the review tool can then read issue data without guessing what keys might exist.

It also defines friendly labels for internal issue codes. For example, a stored value like `spelling_grammar` becomes the more readable label `Spelling/Grammar` when shown to a person.

The main behavior in the file is `format_comment`. It takes one issue and builds a short comment string. The comment starts with a bracketed heading that shows the issue category and severity, then includes the issue description. If a suggested replacement is available, and the caller wants suggestions included, it adds a “Suggested:” line. This keeps review comments consistent, so users see the same clear format no matter which part of the system produced the issue.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns a structured document issue into a plain text comment that can be shown to a reviewer or inserted into a document-review workflow. It makes internal issue codes readable and optionally includes the suggested replacement text.

**Data flow**: It receives an issue dictionary and a yes-or-no setting for whether to include suggestions. It looks up a human-friendly label for the issue type, reads the severity and description, and checks whether the issue has `new_text`. It returns one formatted string; it does not change the issue itself.

**Call relations**: When another part of the document-review scripts needs to present an issue to a person, this function is the final formatting step. Inside, it asks the `DocumentIssue`-shaped dictionary for `new_text` using `get`, so missing or empty suggestion text simply means no suggestion line is added.

*Call graph*: 1 external calls (get).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `command invocation during the document review workflow`

A document review has several steps: outline the document, find claims, fact-check them, find writing or content issues, then submit the final review. This script acts like a checklist keeper for that process. Without it, later review steps would not have a reliable shared record of what has already been found, what still needs checking, and what phase the review is in.

The script stores the current review in `document_review_state.json`, using normal JSON so other tools can read it. It also writes a line-by-line JSON log, which is useful as a trail of what changed and when. Each command loads the state, checks that the incoming data has the fields it needs, updates the state, saves it back to disk, and often prints a structured result. For example, `add-sections` records page ranges and moves the review to claim-finding, while `update-claims` marks claims as verified, refuted, or inconclusive.

The script is forgiving about phase order: it warns if a command is run in an unexpected phase, but usually continues. That matters because human or automated reviewers may need to recover from imperfect sequencing. Its validation helpers are the gatekeepers that stop bad data, such as an unknown severity or an empty location, from corrupting the review record.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the command's final answer as one JSON object. This gives callers both a readable message and structured progress details they can safely parse.

**Data flow**: It receives a message, the current phase, the document name, and optional extra lists such as newly created claims or issues. It wraps them into a single dictionary, converts that dictionary to JSON text, and prints it to standard output.

**Call relations**: The state-changing commands call this at the end of successful work. After commands such as `cmd_init`, `cmd_add_sections`, `cmd_add_claims`, `cmd_update_claims`, `cmd_add_issues`, or `cmd_submit` have saved changes and logged the action, they hand their result to `_emit_result` so the outside caller gets a clean machine-readable summary.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds an audit entry whenever an important command runs. This creates a chronological record of actions, like a receipt book for the review.

**Data flow**: It receives the command name, the phase before and after the command, and extra details such as counts or IDs. It adds the current UTC timestamp, turns the entry into JSON, and appends it as one line to the log file.

**Call relations**: Most commands call this after they inspect or change state. Update commands use it to record what changed, while read commands like `cmd_get_claims` and `cmd_get_issues` use it to record what was queried.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the saved review state from disk. Commands use it when they need the current document, phase, sections, claims, issues, or summary.

**Data flow**: It looks for the configured state file. If the file is missing, it prints an error telling the user to run `init` first and stops the program; otherwise, it reads the JSON text and returns it as a Python dictionary.

**Call relations**: Nearly every command except `cmd_init` starts by calling this. It is the doorway from the saved review record into the command's in-memory work.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk in a readable JSON format. It preserves changes made by commands so later commands can continue from the same point.

**Data flow**: It receives the full state dictionary, converts it to indented JSON text, and writes that text to the configured state file.

**Call relations**: Commands that create or change review data call this before reporting success. For example, `cmd_add_claims` saves new claims, `cmd_update_claims` saves fact-check results, and `cmd_submit` saves the completed status.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user when a command is being run outside the expected review phase. It does not stop the command; it just makes the mismatch visible.

**Data flow**: It receives the current state and the phase the command normally expects. If they differ, it prints a warning to standard error; the state itself is not changed.

**Call relations**: Workflow commands call this near the start. It helps `cmd_add_sections`, `cmd_add_claims`, `cmd_update_claims`, `cmd_add_issues`, and `cmd_submit` guide the user without being so strict that recovery becomes impossible.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an incoming JSON object contains all fields a command needs. This prevents partial or malformed review entries from being saved.

**Data flow**: It receives one item, a list of required field names, and a label such as `Claim` or `Issue`. If any fields are missing, it prints a clear error and exits; if all are present, it returns without changing anything.

**Call relations**: Commands that import structured data call this before using that data. `cmd_add_sections`, `cmd_add_claims`, `cmd_update_claims`, and `cmd_add_issues` rely on it before they create or update state entries.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a fixed set of allowed choices. This keeps fields like claim type, issue type, severity, and claim status consistent.

**Data flow**: It receives the value to check, the allowed set, and the field name. If the value is not allowed, it prints an error showing the valid choices and exits; otherwise, it lets the caller continue.

**Call relations**: Commands call this while processing user-provided JSON. It protects `cmd_add_claims`, `cmd_update_claims`, and `cmd_add_issues` from saving unknown categories that later tools may not understand.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a value into a positive page number and rejects invalid page values. It is used so section page ranges are meaningful.

**Data flow**: It receives a value and a field name. It tries to convert the value to an integer, checks that it is at least 1, and returns the integer; if conversion fails or the number is too small, it prints an error and exits.

**Call relations**: `cmd_add_sections` calls this for section start and end pages. After this helper confirms the numbers are valid, the command can safely compare the page range and save it.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a required text-like value is present and not blank. It also accepts integers and turns them into strings, which is useful for locations like page or paragraph numbers.

**Data flow**: It receives a value and the field name. If the value is not a string or integer, or becomes empty after trimming spaces, it prints an error and exits; otherwise, it returns the value as a string.

**Call relations**: `cmd_add_claims` and `cmd_add_issues` use this for locations. That gives every claim or issue a usable place in the document before it is written into the state file.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which is a more precise pointer into the document when available. It allows the anchor to be missing, but rejects blank or non-text anchors.

**Data flow**: It receives a value and field name. If the value is `None`, it returns `None`; if it is a non-empty string, it returns that string; otherwise, it prints an error and exits.

**Call relations**: `cmd_add_claims` and `cmd_add_issues` call this while building new entries. The result is stored with the claim or issue so later readers can locate the exact text more easily when an anchor exists.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input from either a command-line string or a file. This lets callers choose between passing small data directly and storing larger data in a separate file.

**Data flow**: It receives parsed command-line arguments. If a file path was provided, it reads and returns that file's text; otherwise, it returns the direct `--data` text.

**Call relations**: The commands that accept JSON arrays call this before parsing their input. `cmd_add_sections`, `cmd_add_claims`, `cmd_update_claims`, and `cmd_add_issues` all use it as the common front door for incoming data.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a fresh review for one document. It creates the initial state file with empty sections, claims, issues, and summary.

**Data flow**: It receives command-line arguments containing the document filename. If the filename is blank, it exits with an error; otherwise, it builds a new state dictionary in the `outline` phase, saves it, logs the initialization, and prints a JSON success result.

**Call relations**: `main` dispatches to this when the user runs the `init` command. This is the first command in the normal workflow, and later commands depend on the state file it creates.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document's major sections and their page ranges. Once sections are recorded, the review can move from outlining to finding claims.

**Data flow**: It loads the current state, warns if the phase is not `outline`, reads a JSON array from `--data` or `--file`, and checks every section has a name plus valid start and end pages. It stores each section by name, changes the phase to `find_claims`, saves the state, logs the change, and prints a JSON result listing the added sections.

**Call relations**: `main` calls this for the `add-sections` command. It uses the validation helpers to keep section data clean, then hands persistence to `save_state`, audit recording to `log_action`, and final output to `_emit_result`.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records claims found in a specific section that need fact-checking. A claim is a statement in the document that should be verified, refuted, or marked inconclusive later.

**Data flow**: It loads state, warns if not in `find_claims`, confirms the named section exists, reads a JSON array of claims, and checks each claim's required fields and allowed type. For every valid claim, it increments the claim counter, creates a unique ID, saves the claim as `unverified`, then saves the updated state, logs the new IDs, and prints a JSON result.

**Call relations**: `main` dispatches here for `add-claims`. This command builds on sections created by `cmd_add_sections`, uses shared validators for safety, and prepares records that `cmd_update_claims` will later update during fact-checking.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records fact-check results for existing claims. It changes claims from `unverified` to a final checked status and can attach source URLs used as evidence.

**Data flow**: It loads state, remembers the old phase, warns if not in `fact_check`, and automatically moves from `find_claims` to `fact_check` if needed. It reads a JSON array of updates, verifies each claim exists and each new status is allowed, increments that claim's attempt count, appends any source URLs, saves the state, logs status counts, and prints a JSON result.

**Call relations**: `main` calls this for `update-claims`. It follows after `cmd_add_claims` in the normal workflow and uses the same load, validate, save, log, and emit pattern as the other state-changing commands.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Records problems found in a section, such as factual issues, grammar problems, non-public information, or narrative logic problems. These are the review findings that may require edits.

**Data flow**: It loads state, remembers the old phase, warns if not in `find_issues`, and moves from `fact_check` to `find_issues` when appropriate. It confirms the section exists, reads a JSON array of issues, validates required fields, type, severity, location, and optional anchor, assigns each issue a unique ID, saves the state, logs the new issue IDs, and prints a JSON result.

**Call relations**: `main` dispatches here for `add-issues`. It usually runs after claim checking, and it uses the shared helpers to make sure issue records are complete enough for later reporting or editing.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Marks the review as finished and stores the final summary. This is the closing step of the review workflow.

**Data flow**: It loads state, remembers the old phase, warns if not in `find_issues`, and checks that the summary text is not blank. It sets the phase to `complete`, saves the summary, writes the state file, logs counts of sections, claims, and issues, and prints a JSON completion result.

**Call relations**: `main` calls this for the `submit` command. It ties together everything gathered by earlier commands and uses `save_state`, `log_action`, and `_emit_result` to make the completion durable, auditable, and visible to callers.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved claims to the user, optionally narrowed by status or section. This is a read-only way to inspect what needs checking or what has already been checked.

**Data flow**: It loads state and starts with all claims. If a status or section filter was supplied, it keeps only matching claims, logs the query and result count, then prints each matching claim in a human-readable format, including text, description, location, anchor, and sources when present.

**Call relations**: `main` dispatches here for `get-claims`. Unlike the update commands, it does not save state; it reads through `load_state` and records the lookup through `log_action`.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved issues to the user, optionally narrowed by severity or section. This helps reviewers see the findings that may need attention.

**Data flow**: It loads state and starts with all issues. If a severity or section filter was supplied, it keeps only matching issues, logs the query and result count, then prints each issue with its section, location, text, context, description, and suggested replacement text when present.

**Call relations**: `main` calls this for `get-issues`. It is a read-only companion to `cmd_add_issues`, using `load_state` to fetch the saved issues and `log_action` to leave a record of the inspection.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Prints a dashboard-style summary of the current review. It gives a quick view of the document, phase, section list, claim counts, issue counts, and final summary if one exists.

**Data flow**: It loads state, reads the document name and phase, then prints sections with page ranges, claim totals by status, issue totals by severity and type, and the stored summary when present. It does not change or save anything.

**Call relations**: `main` dispatches here for the `status` command. It depends only on `load_state`, because it is meant to report the current state rather than modify it.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and routes each command to the right function. It is the front desk for this script.

**Data flow**: It builds an argument parser, registers subcommands such as `init`, `add-claims`, `update-claims`, and `status`, parses the user's command-line input, looks up the matching command function, and calls it with the parsed arguments.

**Call relations**: When the script is run directly, execution starts here. `main` does not do review work itself; it sends control to the specific `cmd_*` function that knows how to perform the requested action.

*Call graph*: 1 external calls (ArgumentParser).


### Document annotation outputs
Format-specific command-line scripts read saved review findings and write them back into PDF, PowerPoint, and Excel files as visible annotations or comments.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `command-line run after document review issues have been saved`

This file is a small finishing tool for a document review workflow. Earlier parts of the system record review problems in a JSON state file, which is a plain text file used to store structured data. This script reads those saved issues and makes them visible inside the PDF itself, so a person can open the output PDF and see what needs attention.

The script expects two command-line arguments: the source PDF and the destination PDF. It first loads issues from the review state file named by STATE_FILENAME. If that file is missing, it stops with a clear error, because there is nothing reliable to annotate.

For each issue, it treats the issue location as a page number. It skips issues with invalid page numbers. On a valid page, it chooses a color based on severity: red for high, orange for medium, and yellow for low. It then formats the issue text into a comment, searches the page for the original text, and highlights the found text if possible. If the exact text cannot be found, it still places a note at a safe fallback position near the top-left of the page. This is like putting a sticky note on a printed document even when you cannot underline the exact sentence.

Finally, it saves the modified PDF and reports how many annotations it added.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved review issues from the document review state file. It is used so the annotation step knows what comments need to be placed into the PDF.

**Data flow**: It starts with the expected state filename from STATE_FILENAME. It checks whether that file exists; if not, it prints an error and stops the script. If the file is present, it reads the JSON text, turns it into Python data, takes the values under the "issues" section, and returns them as a list.

**Call relations**: The main annotation flow calls this first inside annotate. It relies on standard file path handling, JSON parsing, and process exit behavior. Once it returns the issue list, annotate uses that list to decide which PDF pages to mark up.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to find where a piece of issue text appears on a PDF page. It returns the PDF text areas that can be highlighted.

**Data flow**: It receives one PDF page and the original text from an issue. First it searches using the first 80 characters, which is enough to identify the text without requiring the whole passage. If that fails, it tries again with the first 30 characters as a looser fallback. It returns whatever matching page areas the PDF library finds.

**Call relations**: annotate calls this for each issue after it has chosen the target page. The results tell annotate whether it can create a highlight around real text or must fall back to placing only a sticky note.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main worker that adds review annotations to a PDF. It connects the saved issue data, the PDF file, the color rules, and the final output file.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the issue list, opens the input PDF, and walks through each issue. For each usable issue, it reads the page number, severity, original text, and formatted comment; then it searches for the text, adds a colored highlight if found, adds a colored sticky note, and counts the annotation. At the end it saves the changed document to the output path, closes the PDF, and prints a summary.

**Call relations**: This function is called by the script’s command-line entry block after the user supplies the input and output filenames. Inside its flow, it calls load_issues to get review data, find_quads to locate text on a page, and format_comment to turn an issue into readable note text. It also uses PyMuPDF, imported as fitz, to open the PDF and create the actual highlights and notes.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `post-review export/annotation`

A PPTX file is really a zip package full of XML files. PowerPoint comments are not added by editing one simple text field; they require several linked XML parts: the comment text, the comment author, the slide-to-comment links, and the package content-type list. This file does that packaging work so review issues can appear inside PowerPoint itself, rather than only in a separate report.

The script starts by reading document_review_state.json, which is the saved review result. Each issue is expected to have a location that can be read as a slide number. The issues are grouped by slide, like sorting sticky notes into piles for slide 1, slide 2, and so on.

To edit the PPTX safely, the script copies the input file to the requested output file, unzips that output into a temporary folder, writes the needed comment XML files, updates the relationship files that tell PowerPoint where those comments live, updates the content-types file so PowerPoint recognizes the new parts, and then zips everything back into a PPTX. Finally, it deletes the temporary folder.

The important thing to know is that this script works directly with PowerPoint’s internal file format. If any of the relationship or content-type entries were missing, the comment files might exist inside the PPTX but PowerPoint would not know how to find or display them.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: This function reads the saved document review results and pulls out the issues that should become PowerPoint comments. It stops the script with a clear error if the review state file is missing, because there would be nothing reliable to annotate.

**Data flow**: It looks for the configured state filename in the current working directory. If the file exists, it reads the JSON text, parses it into normal Python data, and returns the issue records from the state. If the file does not exist, it prints an error message and exits instead of continuing with bad or missing input.

**Call relations**: The main annotate flow calls this first. The issues it returns are then passed into group_by_slide so the rest of the script can create comments on the correct slides.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: This function sorts review issues by the slide they belong to. It lets the script create one comment file per slide instead of treating all comments as one mixed pile.

**Data flow**: It receives a list of issue records. For each issue, it tries to read the issue's location as a slide number. If that works, the issue is added to a dictionary under that slide number; if the location is missing or not a number, that issue is skipped. The result is a slide-number-to-issues map.

**Call relations**: annotate calls this after loading the issues. Its grouped output is used by write_slide_comments to create the slide comment files and by write_author_and_rels to register those comment files in the PPTX package.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: This function finds the largest relationship ID already used in a PowerPoint relationship file. That matters because any new link added to the PPTX needs a fresh ID that does not collide with an existing one.

**Data flow**: It receives the path to a .rels file, which is an XML file listing links between parts of the PPTX package. If the file is missing, it returns 0. Otherwise it reads the XML, looks at each relationship's Id value, extracts the number from values like rId3, and returns the highest number it finds.

**Call relations**: add_relationship calls this right before adding a new relationship. It uses the returned number to choose the next available rId value for the new link.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: This function adds a link inside a PPTX relationship file, creating the file if needed. These links are how PowerPoint knows that a slide has a comment file, or that the presentation has a comment author file.

**Data flow**: It receives a relationship-file path, a relationship type, and a target path. It reads the existing XML or creates a new relationship list if the file does not exist. If a relationship of the same type is already present, it leaves the file unchanged. Otherwise it finds the next available rId, adds a new XML relationship entry, and writes the file back to disk.

**Call relations**: write_slide_comments calls this to connect each slide to its comment XML file. write_author_and_rels calls it to connect the overall presentation to the comment author file. It relies on find_max_rel_id to avoid reusing an existing relationship ID.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: This function creates the actual PowerPoint comment files for each slide that has review issues. It turns each issue into comment text and attaches that comment file to the matching slide.

**Data flow**: It receives the temporary unpacked PPTX folder and the issues grouped by slide. For each slide, it creates an XML comment list, gives each comment an author, timestamp, unique index, fixed position, and formatted text, then writes that XML as ppt/comments/commentN.xml. It also updates the slide's relationship file so PowerPoint can find that comment file. It returns the total number of comments written.

**Call relations**: annotate calls this after unpacking the copied PPTX. For each slide it delegates relationship editing to add_relationship, and it uses format_comment from the models module to turn a review issue into readable comment text. Its comment count is later passed to write_author_and_rels so the author metadata knows the last comment index.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: This function writes the shared metadata that makes the new comments valid PowerPoint comments. It records who the comment author is, links that author file into the presentation, and updates the PPTX content list so PowerPoint recognizes all new comment-related files.

**Data flow**: It receives the temporary unpacked PPTX folder, the total number of comments, and the slide grouping. It writes ppt/commentAuthors.xml with a single author named Flying Object. It adds a presentation relationship pointing to that author file. Then it opens [Content_Types].xml and adds entries for the author file and each slide comment file that is not already listed, before saving the XML back to disk.

**Call relations**: annotate calls this after write_slide_comments has created the per-slide comment files. It uses add_relationship for the presentation-level link, and it completes the package bookkeeping needed for PowerPoint to load the comments correctly.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: This is the main work function for the script. It takes an input PPTX and an output PPTX path, then builds the output file with review comments inserted.

**Data flow**: It starts by loading review issues. If there are none, it prints a message and stops. Otherwise it groups them by slide, copies the input PPTX to the output path, unpacks the output into a temporary folder, writes slide comment files, writes author and content-type metadata, then zips the folder contents back into the output PPTX. Whether the process succeeds or fails, it removes the temporary folder at the end.

**Call relations**: The command-line block calls this when the script is run with an input and output filename. It coordinates the whole flow: load_issues supplies the raw review findings, group_by_slide organizes them, write_slide_comments creates the visible comments, and write_author_and_rels adds the package metadata that lets PowerPoint display them.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review annotation`

This file is a small command-line tool for marking up an Excel workbook after a document review has found problems. It reads a saved review state file, `document_review_state.json`, then writes those issues into a copy of the spreadsheet as Excel comments. Without this script, the review findings would stay outside the spreadsheet, making it harder for a person to connect each issue to the exact cell or sheet it concerns.

The script works like someone placing sticky notes onto a printed spreadsheet. First it loads the list of issues. Then it copies the original workbook to the requested output path, so the input file is not changed. For each issue, it builds readable comment text using `format_comment`, creates an Excel comment with the author name “Flying Object,” and tries to put that comment in the best possible place.

It prefers an exact sheet and cell reference if the issue provides them. If that fails, it searches the named worksheet for the original text. If that also fails, it searches every worksheet. As a last resort, it puts the comment on cell A1 of the first worksheet, adding multiple fallback comments together if needed. Finally it saves the annotated workbook.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved document review results from `document_review_state.json`. It gives the rest of the script a simple list of issues to turn into Excel comments.

**Data flow**: It starts with the expected state filename from the shared constants. It checks whether that file exists; if not, it prints an error and stops the program. If the file is present, it reads the JSON text, extracts the `issues` section, and returns those issue records as a list.

**Call relations**: The main `annotate` function calls this first, before opening the spreadsheet. `load_issues` depends on the JSON parser to turn stored text into Python data, and it stops the whole script early if the needed review state file is missing.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell whose text contains a given piece of original review text. It is used when the script does not have, or cannot use, an exact cell address.

**Data flow**: It receives a worksheet and some target text. It lowercases and trims the target, then looks through every cell in every row. Empty cells are skipped. When a cell’s visible value contains the target text, that cell is returned; if no match is found, the result is `None`.

**Call relations**: The `annotate` function calls this after trying more precise placement options. It is the script’s backup way to connect an issue to the likely cell by matching the issue’s original text against spreadsheet contents.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks up a worksheet by name without caring about uppercase or lowercase differences. It helps the script use an issue’s recorded location even if the capitalization does not exactly match the workbook.

**Data flow**: It receives an open workbook and a location name. It compares that name with each worksheet title in lowercase form. If it finds a matching sheet, it returns that worksheet; otherwise it returns `None`.

**Call relations**: The `annotate` function calls this when an issue includes a sheet-like location. If it finds the right worksheet, later steps can try to place the comment by exact cell anchor or by searching only that sheet first.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a comment directly to a specific cell reference, such as `B12`. It exists because a recorded anchor may be invalid, so the attempt needs to fail safely instead of crashing the whole script.

**Data flow**: It receives a worksheet, a cell reference, and a prepared comment. It asks the worksheet for that cell and assigns the comment to it. If the reference is not usable, it catches the error and returns `False`; if placement succeeds, it returns `True`.

**Call relations**: The `annotate` function calls this when an issue has both a target worksheet and an anchor cell. If this succeeds, no further searching is needed for that issue. If it fails, `annotate` falls back to text-based searching.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function for the script. It creates an annotated copy of an Excel file by adding one comment for each document-review issue.

**Data flow**: It receives an input workbook path and an output workbook path. It loads the review issues, copies the input file to the output location, opens that output workbook, and then processes each issue. For every issue, it formats the issue as comment text, chooses the best cell it can find, attaches the comment, and counts it. At the end it saves the workbook and prints how many comments were added.

**Call relations**: This function is called by the command-line block when the script is run with an input and output filename. It coordinates the smaller helpers: `load_issues` supplies the issue list, `find_worksheet` locates a named sheet, `_place_on_cell` tries an exact cell, and `find_cell` searches by text when exact placement is not possible. It also uses OpenPyXL, the Excel-reading library, to open the workbook and create comments.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).
