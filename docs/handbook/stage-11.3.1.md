# Document review state and artifact annotation scripts  `stage-11.3.1`

This stage is the document review’s record keeper and “write it back” toolset. It is used after or during the review, not to judge the document itself, but to save what was found and place those findings back into files people can open.

The manage_state.py script is the control panel. It stores review progress, claims, issues, and the final summary in a JSON file, which is a simple text format for structured data. It also writes a log so actions can be traced later. constants.py keeps the shared filenames for that state and log in one place. models.py defines what an issue looks like, such as its location and message, and turns it into readable comment text. __init__.py simply lets these scripts be imported as a package.

The annotation scripts are the exporters. annotate_pdf.py adds highlights and sticky-note comments to PDFs. annotate_pptx.py writes issues as PowerPoint comments. annotate_xlsx.py copies an Excel workbook and adds cell comments. Together, they turn saved review notes into visible feedback inside the original kinds of documents.

## Files in this stage

### Package Setup
Package scaffolding makes the document-review scripts importable by the rest of the skill.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `import setup`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. That matters because code elsewhere may want to refer to scripts in this directory using package-style import paths rather than raw file paths. Think of it like putting a label on a drawer: the drawer may hold tools, but the label is what lets the rest of the workshop find it by name. Since this file is empty, it does not define settings, run startup logic, or change behavior directly. Its value is structural: it keeps the project layout import-friendly and makes the `document-review/scripts` area part of the larger Python module tree.


### Artifact Annotation Exporters
Exporter scripts convert saved review issues into visible comments or annotations inside PDF, PowerPoint, and Excel files.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `manual command run after document review issues have been saved`

This file is a small command-line tool for the end of a document review workflow. Earlier steps save review issues into a state file named by STATE_FILENAME, usually document_review_state.json. This script reads those issues and writes a new PDF with annotations added.

The flow is simple. First it loads the saved issues. Each issue is expected to say which page it belongs on, what original text was reviewed, how severe the issue is, and what comment should be shown. The script opens the input PDF using PyMuPDF, a library for reading and editing PDF files. For each issue, it checks that the page number is valid, chooses a color based on severity, and formats the review comment.

Then it tries to find the original text on the page. It searches first with a longer piece of text, then with a shorter prefix if the first search fails. This is like looking for a quote in a book: if the full quote is hard to match, try the first few words. If the text is found, the script highlights it and places a comment icon next to it. If not, it still adds the note at a fixed fallback spot, so the issue is not lost. Finally, it saves the annotated copy of the PDF.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved review results from the state file. It exists so the rest of the script can work with a simple list of issues instead of worrying about where the JSON file lives or whether it exists.

**Data flow**: It starts with the expected state filename from the shared constants. It checks whether that file exists in the current working directory. If the file is missing, it prints an error and stops the script. If the file is present, it reads the JSON text, turns it into Python data, pulls out the saved issues, and returns them as a list.

**Call relations**: The main annotation flow calls this first, before opening or editing the PDF. If this function cannot provide issues, annotation cannot continue, because there would be nothing to place into the document.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to locate the reviewed text on a specific PDF page. It returns the page areas where the text appears, so the script knows exactly what to highlight.

**Data flow**: It receives a PDF page and the original text from an issue. It first searches the page using the beginning of that text up to the primary search length. If that finds nothing, it tries again with a shorter beginning of the text. It returns whatever matching regions the PDF library finds, or an empty result if the text cannot be found.

**Call relations**: The annotation function calls this for each issue after it has chosen the target page. Its result decides whether the script can place a highlight beside the exact text, or whether it must fall back to adding only a sticky note in a default location.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main worker for the script. It reads review issues, opens the source PDF, adds highlights and colored comment notes, and saves the annotated PDF to a new file.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the issues, opens the input PDF, and walks through each issue one by one. For each valid page reference, it chooses a severity color, builds the comment text, searches for the original text, adds a highlight if possible, adds a sticky note, and counts the annotation. At the end it saves the edited PDF to the output path, closes the PDF, and prints how many annotations were added.

**Call relations**: This function ties the whole script together. It calls load_issues to get the review data, calls format_comment to turn each issue into readable note text, calls find_quads to locate text on the page, and uses PyMuPDF to create the actual PDF annotations. When the file is run from the command line, the small __main__ block checks the two required paths and then hands control to this function.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `on-demand document annotation after review`

A PowerPoint `.pptx` file is really a zip package full of XML files. This script uses that fact to add comments directly into the package. Without it, review feedback might stay only in the tool’s JSON state file, instead of appearing where a human expects it: on the slides.

The script first reads `document_review_state.json`, which contains issues found during review. It groups those issues by slide number, using each issue’s `location` field. Then it copies the original PowerPoint to the requested output path, unzips that copy into a temporary folder, and edits the internal XML files.

For each slide with issues, it creates a comment XML file under `ppt/comments/`. Each issue becomes one PowerPoint comment, with fixed author information: “Flying Object” and initials “UFO”. It also adds relationship files, which are like signposts telling PowerPoint which comment file belongs to which slide. Finally, it updates the presentation-level comment author file and the package’s content type list, so PowerPoint knows these new XML parts exist and what they mean.

At the end, the script zips everything back into the output `.pptx` and deletes the temporary folder. The important trick is that it does not use PowerPoint itself; it edits the file structure directly.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: Reads the saved document review state and returns the issues that should become PowerPoint comments. If the state file is missing, it stops the script with a clear error because there is nothing reliable to annotate.

**Data flow**: It looks for the configured state file name in the current working directory. If the file exists, it reads the JSON text, pulls out the `issues` section, and returns those issue records as a list. If the file is not there, it prints an error to standard error and exits the program.

**Call relations**: This is the first step used by `annotate`. The rest of the script depends on the issue list it returns; without it, there are no comments to create.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: Sorts review issues into buckets by slide number. This matters because PowerPoint stores comments per slide, so the script needs to know which comments belong in which slide comment file.

**Data flow**: It receives a list of issue records. For each issue, it tries to read the `location` value as a slide number. Valid slide numbers become keys in a dictionary, and each issue is added to the list for that slide. Issues whose location is missing or not a number are skipped.

**Call relations**: `annotate` calls this after loading issues. The grouped result is then passed to `write_slide_comments` to create slide-specific comment XML and to `write_author_and_rels` so the package advertises those comment files correctly.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: Finds the largest existing relationship ID in a PowerPoint relationship file. This lets the script add a new relationship without accidentally reusing an ID that is already taken.

**Data flow**: It receives the path to a `.rels` XML file. If the file does not exist, it returns 0. If it exists, it parses the XML, reads each relationship’s `Id`, extracts any number from it, and returns the highest number found.

**Call relations**: `add_relationship` calls this when it needs to create a fresh relationship entry. It acts like checking the last ticket number before printing the next ticket.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: Adds a relationship entry to a PowerPoint `.rels` file, creating the file if necessary. A relationship is a small XML pointer that tells PowerPoint how one internal file, such as a slide, connects to another internal file, such as its comments.

**Data flow**: It receives a relationship file path, a relationship type, and a target file path. It opens or creates the relationship XML, checks whether a relationship of that type already exists, and if not, creates a new `rId` using the next available number. It then writes the updated XML back to disk.

**Call relations**: `write_slide_comments` uses this to connect each slide to its comment file. `write_author_and_rels` uses it to connect the whole presentation to the comment author file. It calls `find_max_rel_id` so new relationship IDs do not collide with existing ones.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: Creates the actual per-slide comment XML files inside the unpacked PowerPoint folder. Each review issue becomes a comment that PowerPoint can display on the matching slide.

**Data flow**: It receives the temporary unpacked PowerPoint folder and the issues grouped by slide. For each slide, it builds a comment list XML file, assigns each comment a running number, adds a timestamp, places the comment at position `(0, 0)`, and fills the text using `format_comment`. It writes the slide’s comment file and adds a relationship from the slide to that file. It returns the total number of comments written.

**Call relations**: `annotate` calls this after grouping issues. It hands off relationship creation to `add_relationship`, and it relies on `format_comment` from the models module to turn an issue record into readable comment text.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: Writes the shared comment author information and updates the PowerPoint package so the new comment files are recognized. This is the step that makes the added comments look like official parts of the presentation, not stray files.

**Data flow**: It receives the temporary unpacked PowerPoint folder, the total number of comments, and the slide grouping. It writes `commentAuthors.xml` with the fixed author name and initials. It adds a presentation-level relationship to that author file. Then it opens `[Content_Types].xml` and adds entries for the author file and each slide comment file if they are not already listed.

**Call relations**: `annotate` calls this after `write_slide_comments` has created the comment files. It uses `add_relationship` for the presentation-to-author link, and it completes the package metadata needed by PowerPoint.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: Runs the full annotation process from input PowerPoint to output PowerPoint. This is the main worker used by the command-line script.

**Data flow**: It receives an input `.pptx` path and an output `.pptx` path. It loads issues, stops early if there are none, groups the issues by slide, copies the input file to the output path, unzips that copy into a temporary folder, writes comment files and metadata, then zips the folder back into the output file. Whether the process succeeds or fails, it removes the temporary folder afterward.

**Call relations**: This function ties the whole file together. It calls `load_issues`, `group_by_slide`, `write_slide_comments`, and `write_author_and_rels` in order. The `__main__` block calls it when the script is run from the command line with an input and output PowerPoint path.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review export`

This file is a small command-line tool for making document review results easy to inspect in Excel. A previous review step writes issues into a state file named `document_review_state.json`. This script reads those issues, opens an Excel workbook, and attaches each issue as a comment on the most relevant cell.

The script works like someone placing sticky notes on a spreadsheet. First it copies the input workbook to the requested output path, so the original file is not changed. Then it loads the copied workbook. For each issue, it builds human-readable comment text using `format_comment`, creates an Excel comment with the author name “Flying Object,” and tries to find where that comment belongs.

It uses several clues. If the issue names a worksheet and an exact cell address, it tries that first. If that fails, it searches the named worksheet for the original text. If that still fails, it searches every worksheet. As a last resort, it places the comment on cell A1 of the first worksheet. If A1 already has a fallback comment, it appends the new one instead of replacing it.

Without this file, review issues would stay in a JSON data file that many users would never see. This script bridges that gap by putting feedback directly into the spreadsheet people are reviewing.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: Reads the saved review issues from the review state file. If the file is missing, it stops the script with a clear error because there is nothing to annotate.

**Data flow**: It starts with the expected state filename from configuration. It checks whether that file exists, reads its JSON text, turns that text into Python data, and returns the issue records as a list. If the file is not present, it prints an error to standard error and exits the process.

**Call relations**: The main annotation flow calls this first, before opening the spreadsheet. Its output is the list of issues that `annotate` later turns into Excel comments.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: Searches a worksheet for the first cell whose text contains a given piece of original review text. This helps place a comment near the content it refers to, even when there is no exact cell address.

**Data flow**: It receives a worksheet and some target text. It normalizes the target by trimming spaces and comparing in lowercase, then scans every cell in every row. When it finds a cell whose value contains that target text, it returns that cell. If no matching cell is found, it returns nothing.

**Call relations**: The `annotate` function uses this after direct cell placement fails, first within the worksheet named by the issue and then across all worksheets. It gives `annotate` a likely cell where the review comment should be attached.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: Looks up a worksheet by name without caring about letter case. This lets an issue location like “Sheet1” still match a worksheet named “sheet1.”

**Data flow**: It receives an open workbook and a location name. It compares that name with each worksheet title in lowercase. If a title matches, it returns that worksheet; otherwise it returns nothing.

**Call relations**: The `annotate` function calls this when an issue includes a worksheet location. The returned worksheet becomes the first place where `annotate` tries to put the comment.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: Tries to attach a comment to a specific cell address, such as `B4`. It is a safe helper for the most precise placement method.

**Data flow**: It receives a worksheet, a cell reference, and a prepared Excel comment. It asks the worksheet for that cell and, if the address is valid, sets the cell’s comment and returns true. If the address is invalid or cannot be used, it catches the error and returns false.

**Call relations**: The `annotate` function calls this when an issue provides both a worksheet and an anchor cell. Its true-or-false result tells `annotate` whether it can stop looking or needs to fall back to text search.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: Creates the annotated workbook. It reads all saved review issues, copies the input Excel file, places comments in the best available cells, saves the result, and reports how many comments were added.

**Data flow**: It receives an input workbook path and an output workbook path. It loads review issues, copies the input file to the output file, opens that copied workbook, and processes each issue. For every issue, it formats the issue text, creates an Excel comment, tries exact placement, then worksheet text search, then whole-workbook text search, and finally falls back to cell A1. After all issues are processed, it saves the workbook and prints a summary.

**Call relations**: This is the central flow of the script and is called by the command-line entry block when the user runs the file with input and output paths. It relies on `load_issues` for review data, `find_worksheet`, `_place_on_cell`, and `find_cell` for choosing where comments go, and external Excel helpers from `openpyxl` to read, modify, and save the workbook.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).


### Review State and Issue Data
Shared constants, state-management commands, and issue models define how review progress and findings are stored and rendered.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `startup and whenever document review scripts read or write state/log files`

This is a tiny configuration-style file, but it prevents a common kind of mistake: different parts of the document review tool accidentally using different filenames for the same saved data. It defines one filename for the review state, which is the saved progress of the review, and one filename for the review log, which records review events line by line. Think of it like labeling two folders on a desk: one is for the current checklist, and one is for the running diary of what happened. Other scripts can import these names instead of typing the filenames themselves. If the project ever needs to rename one of these files, the change can happen here rather than being hunted down across the codebase. Without this file, filename spelling would be scattered, making bugs more likely and future changes more tedious.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `active throughout the document review workflow`

A document review has several steps: outline the document, find claims that need checking, verify those claims, find writing or logic issues, and finally submit a summary. This file makes that workflow concrete by storing everything in document_review_state.json. Without it, later review steps would not know what sections exist, which claims have already been found, what has been fact-checked, or which issues should be reported.

Think of it like a clipboard used by several reviewers. The clipboard has the current phase at the top, followed by lists of sections, claims, and issues. Each command updates that clipboard in a controlled way. For example, init starts a blank review, add-sections records the document outline, add-claims creates numbered claim records, update-claims marks those claims as verified or not, add-issues records problems, and submit marks the review complete.

The script is careful about input. It checks that required fields exist, that page numbers are positive, and that values such as claim status or issue severity are from known allowed lists. It writes normal results as JSON for automation-friendly commands, while read-only commands like get-claims, get-issues, and status print a human-readable report. It also appends a JSON-lines log, meaning one JSON object per line, so the review history can be audited later.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints a single machine-readable JSON result for commands that change the review. It combines a friendly message with structured progress information, so another tool can read the output reliably.

**Data flow**: It receives a message, the current phase, the document name, and optional extra details. It wraps them into one dictionary under document_review_progress, turns that dictionary into JSON text, and prints it to standard output.

**Call relations**: The main command functions call this after they successfully change the state, such as after starting a review, adding sections, adding claims or issues, updating claims, or submitting the review. It is the last step that reports the outcome back to the user or calling system.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds an audit trail entry whenever a command does something worth recording. This makes it possible to reconstruct what happened during the review later.

**Data flow**: It receives the command name, the phase before and after the command, and any extra facts such as counts or IDs. It adds the current UTC timestamp, converts the entry to JSON, and appends it as one line to the log file.

**Call relations**: Most command functions call this after reading or changing the review state. It sits beside save_state: save_state keeps the latest clipboard, while log_action keeps the history of how the clipboard changed.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the current review state from disk. Commands use it when they need to know what has already been recorded.

**Data flow**: It looks for the state file. If the file is missing, it prints an error telling the user to run init first and stops the program. If the file exists, it reads the JSON text and returns it as a Python dictionary.

**Call relations**: All commands except init rely on this before doing their work. It is the doorway into the saved review state for adding sections, claims, issues, checking status, and submitting.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk. It preserves the updated review so the next command can continue from the same point.

**Data flow**: It receives the state dictionary, converts it into nicely indented JSON text, and writes that text to the state file.

**Call relations**: Commands that change the review call this after they update the in-memory state. It pairs with load_state: commands load the clipboard, edit it, then save it.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user when a command is being run outside the expected review phase. It does not block the command; it only gives a caution.

**Data flow**: It reads the current phase from the state dictionary and compares it with the expected phase. If they differ, it prints a warning to standard error and leaves the state unchanged.

**Call relations**: Workflow commands call this near the start to catch likely mistakes, such as adding issues before the review has reached the issue-finding phase. The command then continues, so reviewers can still recover or work flexibly.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an input object contains all fields needed for the command to make sense. It prevents half-formed sections, claims, updates, or issues from being saved.

**Data flow**: It receives one input dictionary, a list of required field names, and a label such as Claim or Issue. If any required field is missing, it prints a clear error and stops the program; otherwise it returns without changing anything.

**Call relations**: The add and update commands call this before they create or modify records. It acts as an early gatekeeper before more specific checks, such as allowed status values or valid page numbers.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of the accepted choices for a field. This keeps fields like claim type, claim status, issue type, and severity consistent.

**Data flow**: It receives a value, a set of allowed values, and the field name being checked. If the value is not allowed, it prints an error showing the valid options and stops the program; otherwise it lets the caller continue.

**Call relations**: Commands that add claims, update claims, or add issues use this after checking that the field exists. It protects the state file from spelling variations or unexpected categories.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number into an integer and confirms it is at least 1. This keeps document section ranges meaningful.

**Data flow**: It receives a value that may be text or a number. It tries to convert it to an integer, rejects non-numbers and values below 1, and returns the cleaned integer when valid.

**Call relations**: cmd_add_sections uses this for section start and end pages. After this check, the command can safely compare the two page numbers and save them as numbers.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a required text-like field is not blank. It is used for fields such as a claim or issue location, where an empty value would make the record hard to find in the document.

**Data flow**: It receives any value and the field name. It accepts strings and integers, converts the value to a string, rejects blank text, and returns the cleaned string.

**Call relations**: The claim and issue creation commands call this when they need a usable location. It prepares the value before the record is saved into the state.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which is a more precise pointer into the document. It allows the anchor to be absent, but if present it must be real non-empty text.

**Data flow**: It receives the anchor value and field name. If the value is null, it returns null. If it is a non-empty string, it returns the string. Otherwise it prints an error and stops the program.

**Call relations**: cmd_add_claims and cmd_add_issues call this while building new records. It lets records have no anchor, but prevents broken anchor values from being stored.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input from either a command-line argument or a file. This lets users provide short data directly or larger data through a separate file.

**Data flow**: It receives parsed command-line arguments. If a file path was supplied, it reads and returns that file’s text. Otherwise it returns the text from the --data argument.

**Call relations**: Commands that accept structured JSON input call this before parsing the JSON. The argument parser makes --data and --file mutually exclusive, so this function only has to choose between the two valid sources.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates the first state file with an outline phase and empty places for sections, claims, issues, and the final summary.

**Data flow**: It receives command-line arguments containing the document filename. It rejects an empty filename, builds a fresh state dictionary, saves it, logs the initialization, and prints a JSON success result.

**Call relations**: main dispatches to this when the user runs init. Unlike the other commands, it does not load an existing state; it creates the state that all later commands depend on.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Records the document’s main sections and moves the review from outlining into claim-finding. Sections give later claims and issues a place to belong.

**Data flow**: It loads the current state, warns if the phase is not outline, reads section data from --data or --file, and checks that it is a non-empty list. For each section, it validates the name and page range, stores it by section name, changes the phase to find_claims, saves the state, logs the action, and prints a JSON result listing the added sections.

**Call relations**: main dispatches to this for add-sections. It uses the shared helpers for loading, validating required fields, checking page numbers, saving, logging, and reporting. Later commands such as add-claims and add-issues depend on the sections created here.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds claims found in a specific section. A claim is a statement in the document that may need fact-checking, such as public data or numerical consistency.

**Data flow**: It loads the state, warns if the phase is not find_claims, confirms the named section exists, and reads a JSON array of claims. For each claim, it checks required fields, validates the claim type, cleans the location and optional anchor, assigns the next claim ID, marks the claim unverified, stores it, saves the state, logs the new IDs, and prints a JSON result.

**Call relations**: main dispatches to this for add-claims. It depends on sections having already been added. It creates the records that cmd_update_claims later changes during fact-checking.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records fact-checking results for existing claims. It turns claims from unverified into verified, refuted, or inconclusive, and stores any source links used.

**Data flow**: It loads the state, remembers the phase before the update, warns if not in fact_check, and automatically moves from find_claims to fact_check if needed. It reads a JSON array of updates, checks each claim ID and status, updates the claim status, increments its attempt count, adds source URLs after validating they are strings, saves the state, logs status counts, and prints a JSON result.

**Call relations**: main dispatches to this for update-claims. It consumes claim records created by cmd_add_claims and prepares the review to move toward issue finding. It uses validation helpers to avoid updating unknown claims or storing invalid statuses.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in a section, such as factual issues, grammar problems, private information, or narrative logic problems. These issue records become the concrete findings of the review.

**Data flow**: It loads the state, remembers the old phase, warns if not in find_issues, and automatically moves from fact_check to find_issues if needed. It confirms the section exists, reads a JSON array of issues, validates required fields, issue type, severity, location, and optional anchor, assigns issue IDs, stores each issue, saves the state, logs the created IDs, and prints a JSON result.

**Call relations**: main dispatches to this for add-issues. It depends on the section list and usually follows fact-checking. The records it creates are later counted by submit and shown by get-issues and status.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Marks the review as finished and records the final summary. This is the closing step of the workflow.

**Data flow**: It loads the state, remembers the previous phase, warns if the review is not in find_issues, and rejects an empty summary. It sets the phase to complete, stores the summary, saves the state, counts sections, claims, and issues, logs those totals, and prints a JSON completion result.

**Call relations**: main dispatches to this for submit. It uses the accumulated data from all earlier commands and closes the review by changing the phase to complete.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Prints the saved claims in a readable format, optionally narrowed by status or section. It helps a reviewer inspect what still needs fact-checking or what has already been decided.

**Data flow**: It loads the state and starts with all saved claims. If a status or section filter was supplied, it keeps only matching claims. It logs the lookup, then prints either a no-match message or a formatted list with claim ID, status, type, section, location, text, description, and sources.

**Call relations**: main dispatches to this for get-claims. Unlike the state-changing commands, it does not call save_state or _emit_result; it reads the clipboard and prints a human-friendly view.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Prints the saved issues in a readable format, optionally narrowed by severity or section. It helps a reviewer inspect the problems that have been found.

**Data flow**: It loads the state and starts with all saved issues. If a severity or section filter was supplied, it keeps only matching issues. It logs the lookup, then prints either a no-match message or a formatted list with issue ID, severity, type, section, location, original text, context, description, and suggested replacement text when present.

**Call relations**: main dispatches to this for get-issues. It is a read-only reporting command that depends on issue records created by cmd_add_issues.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Shows a dashboard-style summary of the whole review. It gives a quick answer to: what document is being reviewed, what phase is it in, and how much work has been recorded?

**Data flow**: It loads the state, prints the document name and phase, then summarizes sections, claim counts by status, issue counts by severity and type, and the final summary if one exists. It does not change or save the state.

**Call relations**: main dispatches to this for status. It gathers information created across the workflow and presents it in one place for a human reader.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each command to the right function. It is the front door of the script.

**Data flow**: It builds an argument parser with subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, get-claims, get-issues, and status. It parses the user’s command-line input, looks up the matching command function, and calls it with the parsed arguments.

**Call relations**: This function runs when the script is executed directly. It does not do review work itself; instead it routes the user’s chosen command to the specialized command function that performs that step.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review output formatting`

This file is like a simple form template for problems found during a document review. A review issue might be a spelling mistake, a logic problem, a concern about non-public information, or a number that does not match elsewhere. The `DocumentIssue` type spells out which pieces of information each issue should contain, such as its severity, where it appears, the original text, and any suggested replacement text.

The file also contains a small label lookup table. The raw issue types used in saved data are machine-friendly names like `spelling_grammar`; the table turns them into labels that are nicer for a person to read, like `Spelling/Grammar`.

Finally, `format_comment` turns one issue into a comment string. It starts with a header showing the issue type and severity, then adds the issue description. If there is a suggested replacement and suggestions are enabled, it adds that too. Without this file, other document-review scripts would either duplicate this formatting logic or risk using slightly different field names and comment styles.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns a single document review issue into a clear comment that can be shown to a human reviewer. It gives the comment a readable category label, includes the severity, and optionally adds suggested replacement text.

**Data flow**: It receives an `issue`, which is a dictionary-like review record following the `DocumentIssue` shape, plus a true-or-false choice called `include_suggestion`. It looks up a friendly label for the issue type, reads the severity and description, and checks whether suggested new text is present. It returns one formatted text block; it does not change the issue itself.

**Call relations**: When some other part of the document-review workflow needs display text for an issue, this function is the small final formatter. Inside the function, it uses the issue record's `get` method to safely check for `new_text`, so missing or empty suggestion text simply means no suggestion line is added.

*Call graph*: 1 external calls (get).
