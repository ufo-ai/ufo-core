# Document Review State and Annotation Scripts  `stage-10.3.7`

This stage is the document review “memory and feedback” area. It supports the review process while it runs, and then helps write the results back into the original files. The scripts folder is made importable by its __init__.py file, which is just a marker with no real work of its own. constants.py keeps the agreed file names for the saved review state and the review log, so every script looks in the same place. models.py defines what a review issue looks like, such as where the problem is and what comment should be shown, and turns it into readable feedback.

manage_state.py is the tracker. It is run from the command line and records the review’s progress, from outline through checking, issue finding, and final submission. It saves state in JSON, a simple structured text format, and writes a log of actions. The annotation scripts then act like delivery tools: annotate_pdf.py adds PDF highlights and notes, annotate_pptx.py inserts PowerPoint comments, and annotate_xlsx.py adds Excel cell comments.

## Files in this stage

### Shared Script Foundations
Package marker, constants, and issue model definitions provide the common vocabulary and file names used by the review-management and annotation tools.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `import/package discovery`

Python uses `__init__.py` files as signposts. They tell Python, “this folder is meant to be treated as a package,” which means code elsewhere can import files from this directory in a structured way. In this case, the file is empty, so it does not define any functions, classes, settings, or startup behavior. Its value is mostly organizational: it keeps the `document-review/scripts` folder available to Python’s import system. Without it, depending on the Python version and packaging setup, imports from this folder might fail or behave differently. Think of it like a label on a drawer: the label does not do the work, but it helps the rest of the system know the drawer belongs in the filing cabinet.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This small file is a central label maker for the document review workflow. Instead of having different scripts type out the same file names by hand, it defines two constants: one for the saved state file and one for the log file. The saved state file, `document_review_state.json`, is likely used to remember where a review left off or what has already been processed. The log file, `review_log.jsonl`, is likely used to record review events one line at a time in JSON Lines format, where each line is its own small JSON record. The practical value is consistency. If the project ever needs to rename these files, developers can change the name here instead of hunting through many scripts. Without this file, different parts of the review system might accidentally use slightly different file names, causing missing state, split logs, or confusing review history.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `cross-cutting`

This file is a small shared model for document review results. A review issue is a finding such as a spelling problem, a logic concern, or a piece of information that may need checking. The `DocumentIssue` type spells out the fields each issue is expected to have, such as its ID, severity, description, location in the document, original text, and suggested replacement text. This is like a standard form: every reviewer result should fill in the same boxes so later steps know where to look.

The file also translates internal issue type names, such as `spelling_grammar`, into friendlier labels, such as `Spelling/Grammar`. That keeps machine-friendly names in stored data while making comments easier for people to read.

Finally, `format_comment` builds the actual text shown for one issue. It starts with a bracketed heading containing the readable issue type and severity, adds the issue description, and optionally adds a suggested replacement if one exists. Without this file, other scripts would either have to guess the fields in an issue or repeat their own comment-formatting rules, which could lead to inconsistent review output.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns one document review issue into a human-readable comment. It is used when the system needs to present an issue clearly, with its category, severity, description, and sometimes a suggested fix.

**Data flow**: It receives a `DocumentIssue`, which is a dictionary-like record containing details about one review finding, plus a flag saying whether to include a suggestion. It looks up a friendly label for the issue type, reads the severity and description, and, if allowed and available, reads the issue's `new_text` as the suggested replacement. It returns one formatted text string; it does not change the issue itself.

**Call relations**: When another part of the document-review flow needs display text for an issue, it can call `format_comment` instead of building that text itself. Inside the function, it uses the issue's dictionary-style `get` method to safely check whether suggested replacement text is present before adding a `Suggested:` section.

*Call graph*: 1 external calls (get).


### Review State Management
The state-management command-line tool records review progress and logs actions across the document-review workflow.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `command invocation during document review`

A document review has many moving parts: sections, factual claims to verify, problems to fix, and a final summary. This script is the small control panel for that work. It reads and writes a file named document_review_state.json, which acts like a shared checklist for the review. Without it, later review steps would not know what sections exist, which claims have already been checked, or what issues were found.

The script is used through commands such as init, add-sections, add-claims, update-claims, add-issues, submit, and status. Each command loads the current state, checks that the incoming data is shaped correctly, updates the state, saves it back to disk, and usually writes a structured audit entry to a JSON-lines log. JSON-lines means one JSON record per line, which is useful for reading the history one event at a time.

The review is organized into phases, but the script is forgiving: if a command is run in an unexpected phase, it warns instead of always stopping. Some commands also move the phase forward automatically. For example, adding sections advances the review to claim-finding, and adding issues after fact-checking advances it to issue-finding. Think of it like a clipboard passed through a review team: this file keeps the clipboard readable, updated, and traceable.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints a single machine-readable JSON result for commands that change the review. This gives both a friendly message and structured progress information in one output.

**Data flow**: It receives a message, the current phase, the document name, and optional extra progress details. It wraps them into one dictionary, turns that dictionary into JSON text, and prints it to standard output.

**Call relations**: The commands that create or update review data call this at the end, after saving the state and writing the log. It relies on JSON conversion so the caller can return a clean result that another tool can parse.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds an audit record whenever an important command runs. This creates a timeline of what happened, when it happened, and how the review phase changed.

**Data flow**: It receives the command name, the phase before and after the command, and extra details such as counts or IDs. It adds the current UTC time, converts the record to JSON, and appends it as one line in the log file.

**Call relations**: Most commands call this after they have decided what happened. It uses the clock, the log filename, and JSON writing to leave a durable trail for later debugging or review.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the saved review state from disk. Commands use it to start from the current checklist instead of rebuilding the review from scratch.

**Data flow**: It looks for the state file. If the file is missing, it prints an error and stops the program; otherwise it reads the JSON text and returns it as a Python dictionary.

**Call relations**: Every command except initialization depends on this before doing its work. It is the gateway from the stored document_review_state.json file into the in-memory data that commands edit or display.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk. This makes command changes permanent for the next command invocation.

**Data flow**: It receives the state dictionary, converts it into neatly formatted JSON, and writes it to the state file using UTF-8 text encoding.

**Call relations**: Commands that change the review call this before reporting success. It pairs with load_state: one brings the clipboard in from disk, the other puts the updated clipboard back.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user if they are running a command in a different review phase than expected. It helps catch mistakes without necessarily blocking progress.

**Data flow**: It receives the current state and an expected phase name. If the state's phase is different, it prints a warning to standard error and leaves the state unchanged.

**Call relations**: Phase-sensitive commands call this near the start. The command then continues, so this function acts like a caution sign rather than a locked door.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an input object contains the fields a command needs. This prevents incomplete section, claim, issue, or claim-update data from being saved.

**Data flow**: It receives a dictionary, a list of required field names, and a label for error messages. If any field is missing, it prints a clear error and stops the program; otherwise it returns normally.

**Call relations**: The add and update commands call this before using incoming JSON data. It protects later code from assuming a field exists when it does not.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of the allowed choices for a field. This keeps statuses, types, and severities consistent instead of allowing many spellings or unknown categories.

**Data flow**: It receives a value, a set of allowed values, and the field name. If the value is not allowed, it prints the valid choices and exits; otherwise the caller can safely use the value.

**Call relations**: Claim and issue commands call this when they need controlled vocabulary, such as claim status or issue severity. It is a small guardrail before data is saved.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number-like value into an integer and makes sure it is at least 1. It is used so document section page ranges are usable and sensible.

**Data flow**: It receives a value and a field name. It tries to convert the value to an integer, stops with an error if that fails or if the number is less than 1, and otherwise returns the integer.

**Call relations**: The section-adding command uses this for start and end page numbers. Its output becomes the cleaned page number stored in the review state.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Makes sure a value can be used as a non-empty piece of text. It is used for required location fields, where a blank value would make a claim or issue hard to find in the document.

**Data flow**: It receives a value and a field name. It accepts strings and integers, converts the value to text, rejects empty or whitespace-only text, and returns the cleaned string form.

**Call relations**: Claim and issue creation use this before storing locations. It gives those commands a dependable text location to save.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which is a more precise pointer into the document. The field may be absent or null, but if present it must be meaningful text.

**Data flow**: It receives a value and a field name. If the value is null, it returns null; if it is a non-empty string, it returns that string; otherwise it prints an error and exits.

**Call relations**: Claim and issue creation call this for optional anchors. It keeps precise document pointers clean without requiring every item to have one.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets the JSON input text for commands that accept either direct command-line data or a file path. This lets users choose the most convenient way to provide larger lists.

**Data flow**: It receives parsed command-line arguments. If a file path was supplied, it reads and returns that file's text; otherwise it returns the direct data string from the arguments.

**Call relations**: The commands that add sections, add claims, update claims, or add issues call this before parsing JSON. It hides the difference between --data and --file from the rest of the command.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates the initial state file with an empty set of sections, claims, issues, and a starting phase of outline.

**Data flow**: It receives command-line arguments containing the document filename. It rejects a blank filename, builds a fresh state dictionary, saves it, logs the initialization, and prints a JSON success result.

**Call relations**: The main dispatcher calls this when the user runs init. It is the one command that does not load an existing state first, because its job is to create that state.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document's major sections and advances the review toward finding claims. Sections give later claims and issues a place to belong.

**Data flow**: It loads the current state, reads a JSON array from --data or --file, validates each section name and page range, stores each section by name, changes the phase to find_claims, saves, logs, and prints a JSON result listing the added sections.

**Call relations**: The main dispatcher calls this for add-sections. It depends on the shared validation and disk helpers, then hands the next command a state that now knows the document outline.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in a specific section. These claims become the items that reviewers later verify, refute, or mark inconclusive.

**Data flow**: It loads the state, checks that the named section exists, reads a JSON array of claims, validates required fields and allowed claim types, assigns each claim a new ID, stores it as unverified with zero attempts and no sources, saves the state, logs the additions, and prints a JSON result.

**Call relations**: The main dispatcher calls this for add-claims. It builds on the sections created earlier and prepares data for the claim-updating step.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records fact-checking results for existing claims. It changes claims from unverified to verified, refuted, or inconclusive, and can attach source URLs used during checking.

**Data flow**: It loads the state, moves from find_claims to fact_check if needed, reads a JSON array of updates, checks each claim ID and status, increments the claim's attempt count, appends any source URLs, saves, logs status counts, and prints a JSON result.

**Call relations**: The main dispatcher calls this for update-claims. It consumes claims created by cmd_add_claims and leaves the state ready for issue finding.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in the document, such as factual issues, grammar problems, private information, or narrative logic concerns. These are the concrete fixes or warnings produced by the review.

**Data flow**: It loads the state, moves from fact_check to find_issues if appropriate, checks that the target section exists, reads a JSON array of issues, validates issue type, severity, location, anchor, and required text fields, assigns each issue a new ID, saves, logs, and prints a JSON result.

**Call relations**: The main dispatcher calls this for add-issues. It uses the same storage and validation pattern as claims, but writes into the issues part of the review state.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Marks the review as complete and stores the final summary. This is the closing step that says the document review has finished.

**Data flow**: It loads the state, checks that the summary is not blank, sets the phase to complete, saves the summary, counts sections, claims, and issues, logs the final totals, and prints a JSON completion result.

**Call relations**: The main dispatcher calls this for submit. It is normally used after issue finding, and it uses the shared save, log, warning, and result-output helpers to close the workflow.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved claims for a human reader, optionally narrowed by status or section. It is useful for checking what still needs verification or reviewing past fact-checking work.

**Data flow**: It loads the state, collects all claims, applies optional filters, logs the lookup, and prints either a no-results message or a readable list with IDs, statuses, sections, locations, original text, descriptions, anchors, and sources.

**Call relations**: The main dispatcher calls this for get-claims. Unlike the mutating commands, it does not save changes or use the JSON result wrapper; it is a read-only reporting command.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved issues for a human reader, optionally narrowed by severity or section. It helps reviewers inspect the problems found in the document.

**Data flow**: It loads the state, collects all issues, applies optional filters, logs the lookup, and prints either a no-results message or a readable list with IDs, severity, type, section, location, text, context, description, and suggested replacement text.

**Call relations**: The main dispatcher calls this for get-issues. It is a read-only reporting command that shares the same load-and-log pattern as claim lookup.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Shows a dashboard-style summary of the current review. It gives a quick answer to: what document is being reviewed, what phase are we in, and how much has been recorded?

**Data flow**: It loads the state, prints the document name and phase, then summarizes sections, claim counts by status, issue counts by severity and type, and the final summary if one exists.

**Call relations**: The main dispatcher calls this for status. It only reads the state and formats it for people, so it does not log or save anything.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each user command to the matching function. This is the front door of the script.

**Data flow**: It builds an argument parser, declares all supported subcommands and their options, parses the user's command-line input, looks up the chosen command in a dictionary, and calls the matching command function with the parsed arguments.

**Call relations**: When the script is run directly, this function starts everything. It does not perform review work itself; it routes the request to the command function that knows how to do that specific step.

*Call graph*: 1 external calls (ArgumentParser).


### Document Annotation Writers
Format-specific annotation scripts write saved review issues back into PDFs, PowerPoint decks, and Excel workbooks as visible comments or notes.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `post-review command-line annotation step`

This file solves a practical handoff problem: review findings are useful, but people often need to see them in the original PDF, exactly where the problem appears. The script reads a saved review state file, opens an input PDF, and creates a new output PDF with visual annotations.

It works like a careful reviewer marking up a paper copy. First, it loads issues from a JSON file named by STATE_FILENAME. JSON is a common plain-text data format for structured information. Each issue is expected to include a page location, the original text that triggered the issue, a severity level, and enough information to build a comment.

For each issue, the script checks that the page number is valid. It then chooses a color based on severity: red for high, orange for medium, and yellow for low. It searches the page for the issue text. If it finds the text, it highlights it and places a comment icon beside the highlight. If it cannot find the text, it still adds the note at a default spot on the page, so the issue is not silently lost.

The final result is saved as a separate PDF. Without this file, review results would remain in a separate machine-readable state file instead of becoming visible, shareable PDF markup.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved document review results from the expected state file. It gives the rest of the script a simple list of issues to place into the PDF.

**Data flow**: It starts with no arguments and looks in the current working directory for the configured state filename. If the file is missing, it prints an error and stops the program. If the file exists, it reads the JSON text, pulls out the stored issues, and returns them as a list.

**Call relations**: The main annotation flow calls this first, before opening or editing the PDF. It relies on standard file-path reading and JSON parsing, and it stops the whole script early if the review state is not available.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to find where a quoted piece of issue text appears on a PDF page. It returns the page locations that can be highlighted.

**Data flow**: It receives a PDF page and the original text from an issue. It first searches using a longer slice of the text, which is more specific. If that fails, it searches again using a shorter beginning of the text, which is less precise but more forgiving. It returns whatever matching areas the PDF library finds.

**Call relations**: The annotation function calls this for each issue after choosing the right page. Its result decides whether the script can place a highlight beside the real text, or must fall back to placing only a comment note at a default page position.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main worker that creates the annotated PDF. It turns stored review issues into visible highlights and sticky-note comments.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the issue list, opens the input PDF, walks through each issue, validates the page number, formats the comment text, searches for the matching text, adds a colored highlight when possible, adds a colored comment note, then saves the changed document to the output path. It also prints how many annotations were added.

**Call relations**: This function ties the whole script together. It calls load_issues to get review findings, calls format_comment to turn each issue into reader-friendly note text, calls find_quads to locate text on the page, and uses PyMuPDF through fitz to open, annotate, save, and close the PDF. When the file is run from the command line with the required input and output paths, this is the function that does the real work.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `post-review annotation/export`

A PowerPoint `.pptx` file is really a zipped folder full of XML files. This script treats it that way: it copies the input presentation, unzips the copy into a temporary folder, adds the XML files PowerPoint expects for comments, then zips everything back up again.

The review issues come from `document_review_state.json`, using the shared state filename from `constants`. Each issue is expected to have a `location` that can be read as a slide number. The script groups issues by slide, turns each issue into comment text with `format_comment`, and writes one comments file per slide that needs feedback.

PowerPoint also needs bookkeeping files so it knows the comments exist. The script writes a comment author file for a single author named “Flying Object”, adds relationship files that point slides to their comment files, and updates `[Content_Types].xml`, which is like the package’s table of contents for file types.

Without this file, the document review system could still find issues, but it would not be able to embed them into a PowerPoint deck in a way PowerPoint understands as comments.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: This function reads the saved document review results from `document_review_state.json`. It is the bridge between the review step, which records issues, and the annotation step, which puts those issues into the PowerPoint file.

**Data flow**: It starts with the expected state file name from configuration. If the file is missing, it prints an error and stops the script. If the file exists, it reads the JSON text, looks for the `issues` section, and returns the issue records as a list.

**Call relations**: `annotate` calls this first, before touching the PowerPoint file. The rest of the script depends on the issues it returns; if there are no issues, `annotate` stops early.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: This function sorts review issues into piles by slide number. It makes it possible to create the right comment file for each slide that has feedback.

**Data flow**: It receives a list of issue records. For each issue, it tries to read the issue’s `location` as a number, treating that number as a 1-based slide number. Issues with missing or non-numeric locations are skipped. It returns a dictionary where each slide number points to the issues for that slide.

**Call relations**: `annotate` calls this after loading the issues. The grouped result is then passed to `write_slide_comments`, which creates per-slide comment XML, and to `write_author_and_rels`, which updates the package bookkeeping for those slides.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: This function finds the largest relationship ID already used in a PowerPoint relationship file. It helps the script add a new link without accidentally reusing an existing ID.

**Data flow**: It receives the path to a `.rels` file, which is an XML file listing links between parts of the PowerPoint package. If the file does not exist, it returns `0`. If it exists, it reads each relationship ID, extracts the number inside names like `rId3`, and returns the highest number found.

**Call relations**: `add_relationship` calls this when it needs to create a new relationship. The returned number is used to choose the next safe ID, like taking the next ticket number in a queue.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: This function adds a link inside a PowerPoint `.rels` file, creating the file if needed. PowerPoint relies on these relationship files to know that a slide has a comments file, or that the presentation has a comment-author file.

**Data flow**: It receives a relationship file path, a relationship type, and a target file path. It opens the existing XML relationship file or creates a new empty one. If a relationship of the same type is already present, it leaves the file unchanged. Otherwise, it asks `find_max_rel_id` for the highest existing ID, creates a new `rId` entry, and writes the XML back to disk.

**Call relations**: `write_slide_comments` uses this to connect each slide to its comment XML file. `write_author_and_rels` uses it to connect the presentation to `commentAuthors.xml`. It hands off ID selection to `find_max_rel_id` so the new link fits cleanly with existing PowerPoint links.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: This function creates the actual comment files for the slides. Each review issue becomes a PowerPoint comment with formatted text.

**Data flow**: It receives the temporary unpacked PowerPoint folder and the issues grouped by slide. For each slide, it creates a comments XML file under `ppt/comments`. For each issue on that slide, it adds a comment entry with the shared author ID, the current UTC time, a comment number, a default position, and text from `format_comment`. It also adds the needed slide relationship so PowerPoint can find that comments file. It returns the total number of comments it wrote.

**Call relations**: `annotate` calls this after unpacking the copied `.pptx`. Inside, it calls `format_comment` to turn an issue into human-readable comment text, and `add_relationship` to attach the comment file to the slide. Its comment count is later passed to `write_author_and_rels` so the author metadata can record the last comment index.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: This function writes the PowerPoint package bookkeeping needed for comments to work. It defines who wrote the comments and tells PowerPoint what new XML parts have been added.

**Data flow**: It receives the temporary unpacked PowerPoint folder, the total comment count, and the issues grouped by slide. It writes `ppt/commentAuthors.xml` with one author, “Flying Object”, and records the last comment index. It adds a relationship from the presentation to that author file. Then it opens `[Content_Types].xml` and adds entries for the author file and each slide comments file, unless those entries already exist.

**Call relations**: `annotate` calls this after `write_slide_comments`. It uses `add_relationship` to add the presentation-level link to the author file. Together with the slide comment files, this completes the set of XML pieces PowerPoint needs to show the comments normally.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: This is the main workflow for adding review comments to a PowerPoint file. It coordinates reading issues, modifying the `.pptx` package, and writing the final annotated output file.

**Data flow**: It receives an input `.pptx` path and an output `.pptx` path. It loads review issues; if there are none, it prints a message and stops. Otherwise, it groups issues by slide, copies the input file to the output path, unzips that copy into a temporary folder, writes comment XML and package metadata, then zips the folder back into the output `.pptx`. At the end it prints how many comments were added and always removes the temporary folder.

**Call relations**: This function is called by the command-line block when the script is run with an input and output file. It drives the whole process by calling `load_issues`, `group_by_slide`, `write_slide_comments`, and `write_author_and_rels` in order, with file-copying and zip/unzip work around them.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `manual command-line annotation step after document review`

This file is a small command-line tool for marking up Excel spreadsheets after a document review. The review system saves its findings in a JSON state file, and this script reads those findings, opens an Excel file, and attaches each issue as a cell comment. Without it, spreadsheet review results would stay outside the workbook, making them harder for a person to inspect in Excel or another spreadsheet editor.

The script first loads the saved issues from `document_review_state.json`. For each issue, it builds a readable comment using `format_comment`. Then it tries to put that comment in the most helpful place. If the issue names a worksheet and a cell address, it uses that exact cell. If that does not work, it searches the named worksheet for the original text. If that still fails, it searches every worksheet. As a last resort, it puts the comment on cell `A1` of the first sheet, adding multiple fallback comments together if needed.

It copies the input workbook to the output path before editing, so the original file is left untouched. The result is a new spreadsheet that carries the review notes directly inside the cells, like sticky notes placed on a printed table.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved review issues from the expected state file. It stops the script with a clear error if that file is missing, because there would be nothing reliable to annotate.

**Data flow**: It starts with the fixed state filename from the project constants. It checks whether that file exists, reads its JSON text, and pulls out the stored issue records. It returns those issues as a list; if the file is absent, it prints an error and exits the program.

**Call relations**: The main annotation flow calls this first, before touching the Excel file. It relies on Python’s JSON reader to decode the state file and on `sys.exit` to stop early when the required input is missing.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell whose contents include a given piece of text. It is used when the script knows what text was reviewed but may not know the exact cell address.

**Data flow**: It receives a worksheet and some target text. It normalizes the target by trimming spaces and ignoring letter case, then checks each non-empty cell in the worksheet the same way. It returns the first matching cell, or `None` if no cell contains the text.

**Call relations**: The main annotation function calls this when exact placement is not available or fails. It provides a best-effort way to connect a review issue back to the spreadsheet content that caused it.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks up a worksheet by name without caring about capitalization. It helps the script use the issue’s recorded location when deciding where to place a comment.

**Data flow**: It receives an open workbook and a location string. It compares that string with every worksheet title, ignoring case. It returns the matching worksheet if found, otherwise `None`.

**Call relations**: The main annotation function calls this before trying to place an issue on a specific sheet. If it finds the sheet, later steps can use either the recorded cell anchor or a text search within that worksheet.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a comment to a specific cell reference, such as `B12`. It exists because recorded cell anchors may be missing, invalid, or unusable, and the script needs a safe way to try them.

**Data flow**: It receives a worksheet, a cell reference, and a prepared comment. It asks the worksheet for that cell and, if successful, sets the cell’s comment. It returns `True` when placement worked and `False` when the cell reference cannot be used.

**Call relations**: The main annotation function uses this as its first and most precise placement attempt when an issue includes both a worksheet and an anchor. If this helper says placement failed, the main flow falls back to searching by text.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function: it creates an annotated copy of an Excel workbook and adds one comment for each saved review issue. It is what the command-line entry point calls after checking the user supplied input and output paths.

**Data flow**: It receives an input workbook path and an output workbook path. It loads review issues, copies the input file to the output file, opens the copied workbook, and then processes each issue. For each issue it formats the comment text, tries exact cell placement, then worksheet text search, then whole-workbook text search, and finally falls back to cell `A1`. After all issues are processed, it saves the output workbook and prints how many comments were added.

**Call relations**: This function coordinates all the smaller helpers. It calls `load_issues` to get review data, `find_worksheet` to locate the right sheet, `_place_on_cell` for exact anchors, and `find_cell` for content-based placement. It also hands work to external libraries: `shutil.copy2` preserves the original workbook by copying it, `openpyxl.load_workbook` opens the spreadsheet, `Comment` creates Excel comments, and `format_comment` turns each issue into human-readable feedback.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).
