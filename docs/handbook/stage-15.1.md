# Document and office-file helper scripts  `stage-15.1`

This stage is shared behind-the-scenes support for document workflows. It is a toolbox of command-line scripts used after an agent has inspected or changed office files. The document-review scripts keep the review organized: constants.py names the state and log files, models.py defines a review issue, manage_state.py records review progress in JSON and a log, and __init__.py makes the folder importable. The annotate scripts turn saved findings into visible feedback: PDFs get highlights and notes, PowerPoint files get comments by editing their internal XML, and Excel files get cell comments.

The Office helpers open the “zip packages” inside Word, PowerPoint, and Excel files. DOCX unpack.py and pack.py expand and rebuild Word files, comment.py adds comment records, and accept_changes.py uses hidden LibreOffice to accept tracked edits. PPTX unpack.py and pack.py expand and rebuild presentations, repair.py fixes generated files, slides.py cleans, adds slides, or makes previews, and __init__.py supports imports. XLSX _soffice.py runs LibreOffice safely, while recalc.py recalculates formulas and reports errors. PDF helpers fill real forms, place text using page layout clues, or render pages as images.

## Files in this stage

### Document review state and annotations
Scripts and shared definitions turn saved document-review findings into persistent state, logs, and visible annotations in PDF, PowerPoint, and Excel outputs.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a file named __init__.py tells the interpreter that the surrounding folder should be treated as an importable package. That matters when other parts of the project want to refer to scripts inside this document-review skill using normal Python import paths, rather than treating the folder as just a loose collection of files. Think of it like a label on a drawer: the label does not do the work, but it tells the system that the drawer belongs to an organized set. Because this file has no code, it does not start anything, change settings, or perform document review itself. Its value is structural: without it, depending on the Python version and import style, imports from this folder could fail or behave less clearly.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `after document review, when exporting review findings into a marked-up PDF`

This file is the bridge between an automated document review and a PDF that a human can open and inspect. The review system stores its findings in a state file named by STATE_FILENAME, and this script turns those findings into visible PDF annotations. Without it, the issues would remain separate data, not marked on the document itself.

The script is meant to be run from the command line with an input PDF and an output PDF. First it loads the saved issues. Each issue is expected to include a page location, severity, original text, and enough detail to build a comment. For each valid issue, it opens the matching PDF page, chooses a color based on severity, and formats the comment text.

It then searches the page for the quoted original text. Because PDF text matching can be fragile, it first searches using a longer prefix, then falls back to a shorter one. If it finds the text, it highlights it and places the note beside the highlight. If not, it still adds the note at a fixed fallback spot, like putting a sticky note in the corner when you cannot attach it to the exact sentence. Finally, it saves the annotated copy and reports how many notes it added.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the review results from the saved state file and returns the individual issues. It stops the script with a clear error if the expected state file is missing, because there is nothing useful to annotate without those findings.

**Data flow**: It starts with no direct input, but looks in the current working directory for the file named by STATE_FILENAME. If the file is present, it reads the JSON text, turns it into Python data, takes the values from the "issues" section, and returns them as a list. If the file is absent, it prints an error message to standard error and exits the program.

**Call relations**: The main annotation flow calls this first, before opening or editing the PDF. It uses Path to locate the state file, json.loads to decode its contents, and sys.exit to stop early when the required review data is not available.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to find where a piece of reviewed text appears on a PDF page. It returns the PDF text areas that can be highlighted.

**Data flow**: It receives a PDF page and the original text from an issue. It searches the page using the first 80 characters of that text; if that finds nothing, it tries again with only the first 30 characters. The result is a list of matching page areas, or an empty result if the text cannot be found.

**Call relations**: The annotate function calls this for each issue after it has chosen the page. Its result decides whether annotate can attach a highlight to the exact text, or must place only a sticky note at the fallback position.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function: it copies review issues into a PDF as colored highlights and sticky-note comments. Someone uses it when they want a reviewed document that can be opened in a normal PDF reader with the comments already attached.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the issue list, opens the input PDF, checks each issue for a usable page number, formats the comment, searches for the original text, adds a highlight if possible, adds a colored comment note, then saves the finished PDF to the output path. It changes the PDF document in memory and writes a new annotated file; it also prints a short summary.

**Call relations**: This function drives the whole script. When the file is run from the command line, the main block checks the two required arguments and then calls annotate. Inside, annotate asks load_issues for the review data, asks find_quads to locate text on each page, uses models.format_comment to build the note text, and uses PyMuPDF functions such as fitz.open, fitz.Point, and fitz.Rect to create the actual PDF annotations.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `after document review, when producing an annotated PowerPoint output`

A .pptx file is really a zip folder full of XML files. PowerPoint comments are not added by writing on the slide like text boxes; they live in special comment files, author files, relationship files, and content-type records inside that zip package. This script knows how to add those pieces in the right places.

The script starts by reading document_review_state.json, which is the saved output from the review process. Each issue is expected to include a location, and this script treats that location as a slide number. It groups issues by slide, copies the input presentation to the output path, unzips the copy into a temporary folder, and then writes one comment XML file per slide that has issues.

It also connects each slide to its comment file using a PowerPoint relationship file. Think of these relationship files like a table of contents that tells PowerPoint, “slide 3 has a matching comments file over here.” Finally, it writes the comment author information and updates [Content_Types].xml so PowerPoint recognizes the new comment parts. The edited folder is zipped back into a .pptx file, and the temporary files are removed. Without this script, review issues would stay in JSON form and would not appear as normal PowerPoint comments.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: This function reads the saved review state file and pulls out the list of issues that should become PowerPoint comments. It stops the script with a clear error if the expected state file is missing.

**Data flow**: It starts with the fixed filename from STATE_FILENAME. It checks whether that file exists, reads its text as JSON, looks inside the top-level issues collection, and returns those issue records as a list. If the file is absent, it prints an error to standard error and exits instead of continuing with no data.

**Call relations**: The main annotate flow calls this first, because every later step depends on knowing what issues exist. It relies on JSON parsing and the filesystem, then hands the issue list back to annotate for grouping by slide.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: This function organizes review issues by the slide they belong to. It makes later work simpler by turning one mixed list of issues into separate buckets for slide 1, slide 2, and so on.

**Data flow**: It receives a list of issue records. For each issue, it reads the location field and tries to convert it into a whole-number slide number. Valid slide numbers become keys in a dictionary, and each issue is added to the matching slide’s list. Issues with missing or non-number locations are skipped. The result is a dictionary from slide number to issues for that slide.

**Call relations**: annotate calls this after loading issues. The grouped result is then passed to write_slide_comments, which creates the actual comment files, and to write_author_and_rels, which updates the package records for those comment files.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: This function finds the largest relationship ID already used in a PowerPoint relationship file. It helps the script add a new relationship without accidentally reusing an existing ID.

**Data flow**: It receives the path to a .rels XML file. If the file does not exist, it returns 0. If it exists, it parses the XML, walks through each relationship entry, extracts any number from IDs such as rId5, and remembers the largest number found. It returns that largest number.

**Call relations**: add_relationship calls this when it needs to choose the next safe relationship ID. This is part of the low-level bookkeeping that lets PowerPoint connect slides and presentations to the new comment-related XML files.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: This function adds a link entry to a PowerPoint .rels file. These relationship files tell PowerPoint how one part of the presentation points to another part, such as a slide pointing to its comments file.

**Data flow**: It receives a relationship file path, a relationship type, and a target path. If the relationship file exists, it parses it; otherwise it creates a new Relationships XML document and any needed folders. It first checks whether a relationship of the same type already exists, and if so it leaves the file unchanged. Otherwise it asks find_max_rel_id for the highest existing ID, adds a new Relationship entry with the next ID, and writes the XML back to disk.

**Call relations**: write_slide_comments calls this to connect each slide to its comment file. write_author_and_rels calls it to connect the whole presentation to the comment author file. It is the shared helper that keeps relationship editing consistent in both places.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: This function creates the actual PowerPoint comment XML files for each slide that has review issues. It turns review issue records into visible comment text that PowerPoint can read.

**Data flow**: It receives the temporary extracted PowerPoint folder and the issues grouped by slide. It creates a ppt/comments folder, then loops through each slide’s issues. For each issue, it creates a comment entry with an author ID, timestamp, position, unique comment index, and text produced by format_comment. It writes one comment file per slide, adds the slide-to-comments relationship, and returns the total number of comments written.

**Call relations**: annotate calls this after extracting the presentation. Inside the function, format_comment turns each issue into readable comment text, and add_relationship connects each slide XML file to the new comment XML file. The returned comment count is then used by write_author_and_rels so the author record knows the last comment index.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: This function writes the shared PowerPoint comment author information and updates package-level records so the new comments are recognized. Without these entries, PowerPoint might not know who wrote the comments or even that the comment XML files are part of the presentation.

**Data flow**: It receives the extracted PowerPoint folder, the final comment count, and the grouped slide issues. It writes ppt/commentAuthors.xml with the fixed author name and initials. It adds a presentation relationship pointing to that author file. Then it opens [Content_Types].xml and adds entries for the author file and each slide comment file if they are not already listed. It writes the updated content-types XML back to disk.

**Call relations**: annotate calls this after write_slide_comments has created the per-slide comment files. It uses add_relationship to connect the main presentation to the author file, and it completes the PowerPoint package bookkeeping before annotate zips everything back up.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: This is the main workflow for turning review issues into comments inside a copied PowerPoint file. It coordinates reading issues, editing the .pptx internals, rebuilding the output file, and cleaning up temporary files.

**Data flow**: It receives an input .pptx path and an output .pptx path. It loads issues from the review state file; if there are none, it prints a message and stops. Otherwise it groups issues by slide, copies the input presentation to the output path, extracts that copy into a temporary folder, writes slide comment files, writes author and package records, zips the folder contents back into the output .pptx, prints how many comments were added, and finally deletes the temporary folder.

**Call relations**: The command-line block at the bottom calls annotate when the script is run with an input and output path. annotate is the conductor: it calls load_issues, group_by_slide, write_slide_comments, and write_author_and_rels in order, while standard library tools handle copying, unzipping, walking files, rezipping, and cleanup.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review export/annotation step`

This file is a small command-line tool for marking up an XLSX spreadsheet after a document review has found issues. The review system stores its findings in a JSON state file. This script reads those findings, copies the original spreadsheet to a new output file, and writes each issue as an Excel cell comment.

The goal is practical: instead of asking someone to compare a separate report with a spreadsheet by hand, the issues appear beside the cells they refer to. It first tries to be precise. If an issue names a worksheet and a cell reference, the script places the comment there. If that does not work, it searches the named worksheet for the original text. If that still fails, it searches every worksheet. As a last resort, it puts the comment on cell A1 of the first worksheet, adding multiple missed comments together if needed. This fallback matters because review data may be imperfect, but the tool should not silently drop findings.

The script uses openpyxl, a Python library for reading and writing Excel files, to open the workbook and attach comments. It does not edit the original file directly; it copies it first, which protects the source spreadsheet from accidental changes.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved review findings from the document review state file. It stops the script with a clear error if that file is missing, because there would be nothing reliable to annotate.

**Data flow**: It starts with the expected state filename from configuration. It checks whether that file exists, reads its JSON text, pulls out the stored issue records, and returns them as a list. If the file is absent, it prints an error to standard error and exits the program.

**Call relations**: The main annotation flow calls this first, before touching the spreadsheet. It uses JSON reading and path checks so that annotate can work with a simple list of issues instead of worrying about the storage format.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches a worksheet for the first cell whose visible value contains a given piece of text. It helps place a comment near the text that caused the review issue when an exact cell address is not available or does not work.

**Data flow**: It receives one worksheet and a text snippet. It normalizes the snippet and each cell value by trimming spaces and ignoring letter case, then scans row by row until it finds a match. It returns the matching cell, or returns nothing if no cell contains the text.

**Call relations**: annotate calls this as a backup strategy after trying an exact cell anchor. It may be used on the issue’s named worksheet first, and then across all worksheets if needed.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks for a worksheet by name while ignoring differences in uppercase and lowercase letters. It lets review data refer to a sheet name without needing the capitalization to be perfect.

**Data flow**: It receives an open workbook and a location name. It compares that name with each worksheet title in the workbook, using lowercase versions for a fair comparison. It returns the matching worksheet, or returns nothing if no sheet has that name.

**Call relations**: annotate calls this when an issue includes a worksheet location. If it finds the sheet, the rest of the placement process can try the most likely place before searching the whole workbook.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a comment to a specific cell reference, such as B12. It is used when the review issue already knows the exact cell where the comment should go.

**Data flow**: It receives a worksheet, a cell reference, and a prepared Excel comment. It asks the worksheet for that cell and assigns the comment to it. If the reference is invalid or cannot be used, it returns false instead of crashing; otherwise it returns true.

**Call relations**: annotate calls this before trying text search, because a direct cell reference is the most accurate placement method. Its true-or-false result tells annotate whether it needs to continue with fallback placement.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It copies an input XLSX file to an output path, loads the review issues, and adds each issue as an Excel comment in the best matching place it can find.

**Data flow**: It receives an input spreadsheet path and an output spreadsheet path. It loads the issue list, copies the input file to the output file, opens the copy as an Excel workbook, formats each issue into readable comment text, and tries several placement methods: exact worksheet and cell, text search in the named worksheet, text search across all worksheets, then cell A1 as a fallback. Finally it saves the workbook and prints how many comments were added.

**Call relations**: This function ties the whole script together. It calls load_issues to get review data, uses format_comment to turn each issue into readable text, creates openpyxl Comment objects, relies on find_worksheet, _place_on_cell, and find_cell to choose where comments go, and uses openpyxl and file-copying tools to produce the annotated spreadsheet.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a tiny but useful settings file. The document review feature needs to store two kinds of information on disk. One file, `document_review_state.json`, keeps the current state of the review, like a saved checkpoint. The other, `review_log.jsonl`, records review events over time, with one JSON record per line. By putting these filenames here as constants, the project avoids scattering the same text across many scripts. That matters because filenames are easy to mistype, and changing a filename later would be painful if it appeared in many places. This file acts like a label maker for the rest of the document review code: instead of each part inventing its own label, everyone uses the same official one. There is no active behavior here. It does not read or write files by itself. It simply defines the names other code should use when saving state or appending review logs.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `document review command execution`

A document review has many small pieces: sections, factual claims to verify, issues to fix, source links, and a final summary. This script acts like a checklist notebook for that work. Without it, different steps of the review could lose track of what has already been found, which phase the reviewer is in, or which claim and issue IDs have already been used.

The tool is run from the command line with subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, and status. The main state lives in document_review_state.json. Each command reads that file, checks that the incoming data is shaped correctly, updates the right part of the state, saves it back to disk, and often prints a machine-readable JSON result. It also appends a line to a log file so there is a simple history of what happened.

The review moves through named phases, but the script is forgiving: if a command is used in an unexpected phase, it warns rather than always stopping. That makes it useful for guided workflows where an automated assistant may need structured progress, while still letting a human recover from small ordering mistakes.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the success result for commands that update the review. It wraps a readable message together with structured progress data so another tool can read the output reliably as JSON.

**Data flow**: It receives a message, the current phase, the document name, and optional extra details. It builds one dictionary containing those values, turns it into JSON text, and prints it to standard output.

**Call relations**: After commands such as initializing, adding sections, adding claims, updating claims, adding issues, or submitting finish their work, they call this function to report what changed in a consistent format.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Records a permanent note that a command was run. This gives the review an audit trail, like writing each step in a lab notebook with the time and before-and-after phase.

**Data flow**: It receives the command name, the phase before and after the command, and any extra facts such as counts or IDs. It adds a current UTC timestamp, converts the entry to JSON, and appends it as one line in the log file.

**Call relations**: Most user-facing commands call this after reading or changing state. It does not decide what the command means; it simply records the event that the command hands to it.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the current document review state from disk. Commands use it when they need to know what has already been recorded.

**Data flow**: It looks for the state file. If the file is missing, it prints an error telling the user to run init first and exits; otherwise it reads the JSON text and returns it as a Python dictionary.

**Call relations**: Nearly every command except init begins here, because they need the existing review before they can add claims, update statuses, show a dashboard, or list findings.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the updated review state back to disk. This is what makes a command's changes last after the script exits.

**Data flow**: It receives the full state dictionary, converts it into nicely indented JSON, and writes that text to the state file.

**Call relations**: Commands that change the review call this after modifying the state and before they log and report the result. Read-only commands do not use it.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user if a command is being run at a surprising point in the review process. It protects the intended workflow without completely blocking recovery.

**Data flow**: It receives the current state and the phase the command normally expects. If the current phase is different, it prints a warning to standard error and leaves the state unchanged.

**Call relations**: Phase-sensitive commands call this before making their changes. They continue afterward, so this function acts as a caution sign rather than a locked gate.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an incoming JSON object contains the fields the command needs. This prevents half-formed sections, claims, issues, or claim updates from being saved.

**Data flow**: It receives a dictionary, a list of required field names, and a label such as Section or Claim. If any fields are missing, it prints a clear error and exits; otherwise it returns nothing and the caller continues.

**Call relations**: Commands that accept structured JSON call this early in their loops, before using the incoming values. It is one of the shared guardrails for data quality.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a known set of allowed choices. This keeps fields such as claim type, claim status, issue type, and severity consistent.

**Data flow**: It receives a value, the allowed set, and the field name. If the value is not allowed, it prints an error with the valid choices and exits; otherwise the caller can safely store that value.

**Call relations**: Claim and issue commands call this when they need controlled vocabulary. That way later status counts and filters can rely on consistent spelling.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number into an integer and confirms it is at least 1. It is used so section page ranges cannot contain empty, negative, or non-number pages.

**Data flow**: It receives a value and a field name. It tries to convert the value to an integer, exits with an error if conversion fails or the number is below 1, and returns the valid integer.

**Call relations**: The add-sections command uses this for start_page and end_page before saving a section. It supplies clean page numbers for later status and display output.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a required text-like value is not blank. It accepts strings and integers, then stores them as text, which is useful for locations that might be written as numbers.

**Data flow**: It receives a value and a field name. If the value is not a string or integer, or becomes empty after trimming spaces, it prints an error and exits; otherwise it returns the value as a string.

**Call relations**: The add-claims and add-issues commands use this for location fields. It ensures every saved claim or issue can point back to a place in the document.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor, which is a more precise pointer into the document text. The anchor may be absent, but if present it must be meaningful text.

**Data flow**: It receives a value and a field name. If the value is null, it returns null; if it is a non-empty string, it returns that string; otherwise it prints an error and exits.

**Call relations**: The add-claims and add-issues commands use this after validating locations. It lets entries have an optional fine-grained marker without allowing blank placeholders.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets the JSON input for commands that can receive data directly or from a file. This lets users choose between passing small data on the command line or loading larger data from disk.

**Data flow**: It receives parsed command-line arguments. If a file path was supplied, it reads and returns that file's text; otherwise it returns the direct --data text.

**Call relations**: Commands that add sections, add claims, update claims, or add issues call this before parsing JSON. It hides the difference between --data and --file from the rest of each command.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new review for a document. It creates the first empty state file with the review set to the outline phase.

**Data flow**: It receives command-line arguments containing the document filename. It rejects a blank filename, builds a fresh state with empty sections, claims, issues, counters, and summary, saves it, logs the initialization, and prints a JSON result.

**Call relations**: The main dispatcher calls this when the user runs init. It is the only command that does not need an existing state file, because it creates the state that all later commands read.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document's main sections and moves the review toward claim finding. Sections give later claims and issues a place to belong.

**Data flow**: It loads the current state, reads a JSON array of sections, checks each section has a name and valid page range, stores the sections by name, changes the phase to find_claims, saves the state, logs the change, and prints a structured result.

**Call relations**: The main dispatcher calls this for add-sections. It relies on shared helpers to read data, validate required fields and page numbers, save the new state, log the action, and emit the final JSON response.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in one section of the document. These claims become the checklist for later fact checking.

**Data flow**: It loads the state, confirms the named section exists, reads a JSON array of claims, validates required fields and allowed claim types, assigns each claim a new ID, marks it unverified, stores it under that section, saves the state, logs the IDs, and prints the newly created claims.

**Call relations**: The main dispatcher calls this for add-claims. It uses the validation helpers as gatekeepers, then hands the finished state to save_state, log_action, and _emit_result.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the outcome of fact checking. It changes claims from unverified to verified, refuted, or inconclusive, and can attach source URLs used during checking.

**Data flow**: It loads the state, moves from find_claims to fact_check if needed, reads a JSON array of updates, checks each claim ID exists and each status is allowed, increments the claim's attempt count, appends source URLs, saves the state, logs status counts, and prints a summary.

**Call relations**: The main dispatcher calls this for update-claims. It sits between claim collection and issue finding, using helpers for data input and validation before saving and reporting the updated claim statuses.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in the document, such as factual errors, grammar problems, private information, or narrative logic issues. These issues are the concrete items that need attention before completion.

**Data flow**: It loads the state, moves from fact_check to find_issues if needed, confirms the section exists, reads a JSON array of issues, validates required fields, issue type, severity, location, and optional anchor, assigns each issue a new ID, stores it, saves the state, logs the new IDs, and prints a result.

**Call relations**: The main dispatcher calls this for add-issues. It reuses the same input, validation, saving, logging, and result-output pattern used by the other state-changing commands.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Marks the review as finished and stores the final summary. This is the closing step of the workflow.

**Data flow**: It loads the state, checks that the summary text is not blank, sets the phase to complete, saves the summary, writes the state to disk, logs the final counts of sections, claims, and issues, and prints a completion result.

**Call relations**: The main dispatcher calls this for submit. It follows the issue-finding phase and uses the shared save, log, warning, and JSON-output helpers to close the review cleanly.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved claims in a readable text format, optionally narrowed by status or section. It is a lookup tool for reviewers who want to inspect the claim checklist.

**Data flow**: It loads the state, starts with all claims, applies any requested status or section filters, logs the lookup and result count, then prints either a no-match message or each matching claim with its section, location, text, description, and sources.

**Call relations**: The main dispatcher calls this for get-claims. Unlike update commands, it does not save state; it only reads the state and records that the lookup happened.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved issues in a readable text format, optionally narrowed by severity or section. It helps a reviewer see what still needs fixing or reporting.

**Data flow**: It loads the state, starts with all issues, applies any requested severity or section filters, logs the lookup and result count, then prints either a no-match message or each matching issue with its location, text, context, description, and suggested replacement.

**Call relations**: The main dispatcher calls this for get-issues. It is a read-only reporting command that depends on load_state for the current data and log_action for the audit trail.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Prints a dashboard-style overview of the current review. It summarizes the document, phase, sections, claim statuses, issue counts, and final summary if one exists.

**Data flow**: It loads the state, reads the document name and phase, then counts and formats sections, claims by status, and issues by severity and type. It prints this information to the screen without changing the state.

**Call relations**: The main dispatcher calls this for status. It is the simplest read-only command: it only needs load_state, then turns the stored JSON into a human-friendly progress report.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each user command to the right function. It is the front door of the script.

**Data flow**: It builds an argument parser, defines all supported subcommands and their options, parses what the user typed, looks up the matching command function, and calls it with the parsed arguments.

**Call relations**: When the script is run directly, this function starts everything. It does not perform review work itself; it routes commands like init, add-claims, get-issues, and status to the specialized functions that do.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review formatting`

This file is like a simple form template for document review problems. A review issue might be a spelling mistake, a logic problem, a possible non-public information leak, or a number that does not match elsewhere. The `DocumentIssue` type spells out the fields every issue is expected to have, such as its ID, severity, description, where it appears, the original text, and a suggested replacement. This matters because later code can rely on the same names instead of guessing how an issue is stored.

The file also keeps a small lookup table called `ISSUE_TYPE_LABELS`. It turns internal issue codes, like `spelling_grammar`, into labels a person can read, like `Spelling/Grammar`.

Finally, `format_comment` turns one issue into the text of a review comment. It starts with a bracketed heading showing the issue type and severity, then adds the issue description. If there is suggested replacement text and the caller wants suggestions included, it adds that too. Without this file, other parts of the review workflow would either duplicate this formatting logic or risk producing inconsistent comments.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: Builds a human-readable comment from one document review issue. Someone would use it when they need to show a reviewer or document author what the problem is and, optionally, what replacement text is suggested.

**Data flow**: It receives an issue dictionary and a yes-or-no setting for whether to include suggestions. It reads the issue type, severity, description, and possible new text. It converts the issue type into a friendly label when one is known, assembles the comment as separate lines, and returns one finished text string.

**Call relations**: This function is the presentation step for a `DocumentIssue`: it takes the structured issue data and turns it into prose. While building the comment, it checks whether the issue has suggested replacement text before adding a `Suggested:` line, so callers can get either a full recommendation or a shorter issue-only comment.

*Call graph*: 1 external calls (get).


### DOCX cleanup and packaging
Word helpers accept tracked changes, add comment XML, and move DOCX files between editable unpacked folders and rebuilt document packages.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `document cleanup run`

This file solves a practical document-cleanup problem: a .docx file may contain tracked edits, and the system needs an output copy where those edits are accepted as final text. Instead of trying to edit the Word file format directly, the script asks LibreOffice to do the job, because LibreOffice already understands tracked changes in DOCX files.

The script first checks that the input exists and is really a .docx file. It then copies the input to the requested output path, so the original document is not changed. Next it prepares a small LibreOffice Basic macro. A macro is a tiny script that LibreOffice can run inside a document. This macro tells LibreOffice to accept all tracked changes, save the document, and close it.

To keep this automation separate from a user’s normal LibreOffice settings, the script uses a temporary LibreOffice profile under /tmp. Think of this like giving LibreOffice a disposable workspace with just the one tool it needs installed.

One important behavior is that LibreOffice may hang after successfully saving the file. Because of that, if the process times out, this script treats the timeout as success. That is unusual, but intentional: the document has often already been saved by then.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when starting LibreOffice. It forces LibreOffice to use a background-friendly display mode so it can run without a normal desktop window.

**Data flow**: It starts with the current process environment variables, copies them, then adds the SAL_USE_VCLPLUGIN setting with the value svp. The result is a dictionary of environment variables that can be passed to LibreOffice when it is launched.

**Call relations**: Whenever this script starts LibreOffice, the caller asks _soffice_env for the right environment first. _ensure_macro uses it while preparing the LibreOffice profile, and accept_tracked_changes uses it when running the macro against the copied document.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: Creates the command-line argument that tells LibreOffice to use this script’s temporary profile. This keeps the automation’s macro and settings away from any normal user profile.

**Data flow**: It reads the fixed profile directory path and formats it into the special LibreOffice argument -env:UserInstallation=file://.... The output is a single string ready to be placed in a LibreOffice command.

**Call relations**: _ensure_macro calls this when it starts LibreOffice once to initialize the temporary profile. accept_tracked_changes calls it again when it runs LibreOffice to open the document and execute the macro.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the Basic macro installed before the document is processed. Without this macro, LibreOffice would open but would not know to accept the tracked changes.

**Data flow**: It checks whether the expected macro file already exists and contains the AcceptAllTrackedChanges macro. If it is missing, it starts LibreOffice briefly to create the profile folders, creates the macro directory if needed, and writes the macro XML file. It returns True after the macro is available.

**Call relations**: accept_tracked_changes calls _ensure_macro after copying the input document and before asking LibreOffice to modify it. Inside this setup step, _ensure_macro gets the LibreOffice profile argument from _profile_arg, gets the safe headless environment from _soffice_env, and uses subprocess.run to launch LibreOffice for profile initialization.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: Creates an output copy of a DOCX file with all tracked changes accepted. This is the main reusable function for other code or for the command-line wrapper at the bottom of the file.

**Data flow**: It receives an input file path and an output file path. It turns them into Path objects, checks that the input exists and has a .docx extension, creates the output folder if needed, and copies the input file to the output location. It then ensures the LibreOffice macro is installed and launches LibreOffice in headless mode to run that macro on the copied file. It returns a pair whose first value is always None and whose second value is a human-readable success or error message. It also changes the output file on disk by saving it with tracked changes accepted.

**Call relations**: This is the central flow of the script. It calls _ensure_macro to prepare LibreOffice, uses _profile_arg and _soffice_env to build the LibreOffice command safely, uses shutil.copy2 to preserve the original file while making an editable output copy, and uses subprocess.run to start LibreOffice. If LibreOffice times out, this function still reports success because the macro often saves the document before LibreOffice gets stuck.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `manual DOCX editing script run`

A DOCX file is really a zipped folder full of XML files. Word comments are not stored in just one place: there is the main comment text, newer metadata for threaded comments, durable IDs, timestamps, relationships, and content type registrations. This file is the tool that writes those supporting pieces so Word can recognize a new comment.

The main job is done by insert_comment. It checks that the unpacked document has a word folder, creates random-looking paragraph and durable IDs, and records the current UTC time. If this is the first comment, it copies template comment files into the document and updates the DOCX bookkeeping files that tell Word those files exist. Then it builds four XML snippets: the visible comment body, extended threading information, an ID mapping, and extra timestamp metadata. Each snippet is appended to the right XML file.

For replies, it also looks up the parent comment’s paragraph ID so Word can connect the reply to the original comment. One important limitation: the script only adds the comment records. After it runs, the user must still insert comment range markers into document.xml, using the printed instructions, so Word knows which text the comment belongs to. Also, the comment text is expected to already be safe XML text, such as using &amp; instead of a raw ampersand.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character uppercase hexadecimal ID, such as a Word paragraph marker might use. The script uses these IDs so newly added comment records can refer to each other consistently.

**Data flow**: It takes no input. It asks Python for a random number in Word’s expected range, formats that number as eight hexadecimal characters, and returns the text form of that ID.

**Call relations**: insert_comment calls this when starting a new comment. The returned values become the paragraph ID and durable ID that later XML-building functions place into the comment metadata.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces curly quotation marks and apostrophes with XML character references. This keeps those characters written in a form that is safe and predictable inside the saved XML.

**Data flow**: It receives a text string. It scans for smart quotes like “ ” ‘ ’, replaces each one with its numeric XML spelling, and returns the changed string.

**Call relations**: _serialize_xml calls this just before XML bytes are written back to disk. It is a final cleanup step after the XML tree has been turned into text.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an editable XML tree. This lets the rest of the script work with XML elements instead of raw text.

**Data flow**: It receives a file path. It reads the file’s bytes, gives them to lxml, an XML parsing library, and returns the root XML element that represents the document structure.

**Call relations**: _append_element_to_file uses it before adding new child elements. _ensure_registrations uses it to inspect and update DOCX bookkeeping files. _resolve_parent_paragraph uses it to search existing comments.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an edited XML tree back into bytes that can be saved to a file. It also applies the curly-quote escaping step before writing.

**Data flow**: It receives the root of an XML tree. It serializes that tree with an XML declaration and UTF-8 encoding, converts smart quotes into XML character references, and returns the final bytes.

**Call relations**: _append_element_to_file calls this after it has added a new XML element. The result is what gets written back over the original XML file.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main XML record for a Word comment: who wrote it, when, its numeric comment ID, and the comment text. This is the part that stores the actual human-readable comment.

**Data flow**: It receives the comment ID, author name, author initials, timestamp, generated paragraph ID, and body text. It creates a Word comment XML element containing a paragraph, a comment reference marker, and a text run, then returns that element without writing it to disk.

**Call relations**: insert_comment calls this after preparing IDs and the timestamp. The returned element is handed to _append_element_to_file so it can be added to comments.xml.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the XML record Word uses for extended comment information, especially whether a comment is a reply to another comment. This is what helps support threaded comments.

**Data flow**: It receives the new comment’s paragraph ID and, optionally, the parent comment’s paragraph ID. It creates a commentEx XML element marked as not done, adds the parent link if one exists, and returns the element.

**Call relations**: insert_comment calls this after it has either found the parent paragraph ID or decided the comment is not a reply. The returned element is appended to commentsExtended.xml.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML link between a comment paragraph ID and a durable ID. The durable ID is a stable identifier Word can use in newer comment metadata.

**Data flow**: It receives the paragraph ID and durable ID. It creates a commentsIds XML element connecting the two values and returns that element.

**Call relations**: insert_comment calls this once the new IDs have been generated. The element is then appended to commentsIds.xml so the newer Word metadata can find the comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the XML record that stores newer extensible comment metadata, including the durable ID and UTC timestamp. This supports the extra comment data used by modern Word versions.

**Data flow**: It receives a durable ID and timestamp. It creates a commentExtensible XML element with those values and returns it.

**Call relations**: insert_comment calls this near the end of the comment-writing process. The returned element is appended to commentsExtensible.xml alongside the other comment metadata.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the paragraph ID for an existing parent comment. Replies need this hidden paragraph ID, not just the visible numeric comment ID, to be connected correctly in Word’s metadata.

**Data flow**: It receives the path to comments.xml and a parent comment ID. It parses the comments file, searches for the matching comment, looks inside it for a paragraph with a Word paragraph ID, and returns that ID if found; otherwise it returns nothing.

**Call relations**: insert_comment calls this only when the new comment is meant to be a reply. Its result is passed into _build_extended_element so the reply can point back to its parent.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to the end of an existing XML file and saves the file. It is the small write step used for each comment-related file.

**Data flow**: It receives a file path and an XML child element. It parses the existing file, appends the child to the root element, serializes the updated XML, and writes the bytes back to the same path.

**Call relations**: insert_comment calls this repeatedly: once for the main comment and then for each supporting metadata file. It relies on _parse_xml_file to read and _serialize_xml to prepare the updated content for disk.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Updates the DOCX bookkeeping files so Word knows the comment XML files exist and what kind of files they are. Without these registrations, the comment files might be present but ignored by Word.

**Data flow**: It receives the unpacked DOCX base directory. It looks for document.xml.rels, which lists related files, and [Content_Types].xml, which labels file types. If comment registrations are missing, it adds relationship entries and content type overrides for all four comment-related XML files, then writes the changed files back.

**Call relations**: insert_comment calls this only when it is creating the comment files for the first time. It prepares the package-level wiring before the script appends actual comment data.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds one comment, or one threaded reply, to the supporting XML files inside an unpacked DOCX directory. This is the main reusable operation behind the script.

**Data flow**: It receives the path to an unpacked DOCX folder and a CommentSpec containing the comment ID, text, author, initials, and optional parent ID. It checks for the word folder, creates needed IDs and a timestamp, copies template files if this is the first comment, updates registrations, builds the four XML records, appends them to their files, and returns the new paragraph ID plus a success or error message. If a requested parent comment cannot be found, it returns an error; notably, by that point the main comments.xml entry has already been appended.

**Call relations**: The command-line section calls this after reading user arguments. Inside, it coordinates all helper functions: _make_hex_tag creates IDs, the build functions create XML pieces, _resolve_parent_paragraph links replies to parents, _ensure_registrations prepares first-time DOCX wiring, and _append_element_to_file saves each new piece.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`entrypoint` · `manual document packaging / command run`

A DOCX file is really a ZIP archive full of XML files and related assets. This file is a small command-line tool for taking a folder version of that archive and packing it back into a .docx file that can be opened or shared. Without it, a workflow that edits DOCX contents as normal files would have no simple way to rebuild the final document.

The main flow is simple. First, it checks that the input is actually a directory and that the output name ends in .docx. Then it copies the whole input folder into a temporary staging area, like making a safe workbench copy instead of touching the original. In that staging copy, it finds XML files and relationship files, then removes whitespace that is only there for formatting and not meaningful content. It is careful not to strip whitespace inside Word text elements, because spaces in document text can matter.

Finally, it creates the output folder if needed and writes every staged file into a compressed ZIP archive with a .docx name. If XML cleanup fails, it prints a clear error to standard error and raises the problem instead of silently producing a broken document.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing routine. It takes a folder that represents an unpacked DOCX document, cleans the XML inside it, and writes a finished .docx file.

**Data flow**: It receives an input directory path and an output file path. It first checks that the input exists as a folder and that the output has the .docx extension; if either check fails, it returns no file path and an error message. If the checks pass, it copies the input into a temporary staging folder, asks _strip_xml_whitespace to clean each XML and .rels file, then writes all staged files into a compressed .docx archive. It returns the output path and a success message.

**Call relations**: This function is the center of the script. The command-line block calls it after reading the user’s arguments. During its work, it relies on temporary directory creation for a safe workspace, shutil.copytree to duplicate the source folder, _strip_xml_whitespace to clean document XML, and zipfile.ZipFile to build the final DOCX archive.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML-like file by removing whitespace that only exists between tags. It preserves whitespace inside Word text fields, where spaces can be part of the actual document text.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through every element, skips real text-bearing elements, removes blank-only text and tail whitespace elsewhere, and removes special non-normal XML child nodes if found. It then writes the cleaned XML bytes back to the same file. If parsing or writing fails, it prints an error naming the file and passes the exception upward.

**Call relations**: pack_docx calls this helper for every staged .xml and .rels file before the archive is built. This means the cleanup happens on the temporary copy, not the original source folder. The helper uses lxml.etree.parse to read XML, lxml.etree.tostring to turn the cleaned tree back into bytes, and Path.write_bytes to overwrite the staged file.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual DOCX unpacking and preparation for XML editing`

A .docx file is really a ZIP archive full of XML files. This file is a small command-line tool that opens that archive, extracts it into a directory, and then makes the important XML easier to read and compare. Without this, a Word document would stay as one opaque file, and small text changes could be hidden inside messy, hard-to-review XML.

The main flow is simple: check that the input exists and ends in .docx, unzip it, pretty-print the XML with indentation, optionally tidy Word’s tracked-change records, optionally merge neighboring text runs with the same formatting, and finally replace curly quote characters with explicit XML character references. A “run” is Word’s small chunk of text with formatting attached; Word often splits one sentence into many runs for reasons that are not meaningful to humans. Merging those runs is like taping together pieces of the same torn label so the text is easier to read.

The script is deliberately cautious. Some formatting cleanup functions ignore individual XML parsing errors instead of stopping the whole unpack. The tracked-change and run-merging steps focus on word/document.xml, the main body of the Word document, because that is where most editable content lives.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker for the script. It unzips a .docx file into a folder, makes the XML more readable, optionally simplifies Word-specific noise, and returns both a structured result and a human-readable message.

**Data flow**: It starts with an input file path, an output folder path, and two true/false options for cleanup. It checks the file, creates the destination folder, extracts the ZIP contents, finds XML-like files, rewrites them in a cleaner form, applies document.xml cleanup when present, then reports how many XML files were found and how many simplifications were made. If the input is missing, not a .docx, or not a valid ZIP archive, it returns no result and an error message instead.

**Call relations**: When the script is run from the command line, this function is the central step called after argument parsing. It delegates the detailed cleanup work to _indent_xml, _coalesce_tracked_changes, _merge_adjacent_runs, and _replace_curly_quotes, then wraps their outcomes in an UnpackResult and summary string.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This rewrites one XML file with consistent indentation, making it much easier for a person to read. It is a cosmetic cleanup step, not a content-changing step.

**Data flow**: It receives a path to an XML file, parses the file into an XML tree, asks the XML library to add two-space indentation, and writes the formatted XML back to the same file. If parsing or writing fails, it quietly leaves that file alone.

**Call relations**: unpack_docx calls this once for each extracted XML or relationship file. It runs early, before the Word-specific cleanup, so the unpacked folder is readable from the start.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This changes literal curly quote characters into XML character references. That keeps these special punctuation marks visible and stable in plain text XML files.

**Data flow**: It reads one XML file as text, looks for curly single or double quotes, and, if it finds any, writes the file back with each quote replaced by its numeric XML form. If the file cannot be read or written, it quietly does nothing.

**Call relations**: unpack_docx calls this near the end for every extracted XML or relationship file. It is the final polish after indentation, tracked-change coalescing, and run merging.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This simplifies the main Word document XML by combining neighboring text runs that have the same formatting. It makes Word’s internal text representation less fragmented and easier to review.

**Data flow**: It receives the path to word/document.xml. If the file exists, it parses it, removes proofing-error markers, removes Word revision ID attributes from runs, finds every parent element that contains runs, and asks _merge_runs_in to combine compatible neighbors. If anything was merged, it writes the updated XML back and returns the number of absorbed runs.

**Call relations**: unpack_docx calls this only for document.xml and only when run merging is enabled. It does the document-wide setup, while _merge_runs_in performs the actual merging inside each run container.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This creates a stable fingerprint for a run’s formatting. Two runs with the same fingerprint can be treated as having the same formatting for merge purposes.

**Data flow**: It receives one Word run element, looks for its run-properties child, and converts that formatting element into a canonical XML string. If the run has no formatting child, it returns no fingerprint.

**Call relations**: _merge_runs_in calls this while scanning neighboring runs. Its output is the comparison key that tells _merge_runs_in whether two adjacent runs are safe to combine.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This merges compatible runs within one parent XML element. It is the local, hands-on part of the run cleanup process.

**Data flow**: It receives an XML container, walks through its direct children, and groups consecutive Word run elements whose formatting fingerprints match. For each group with more than one run, it keeps the first run, moves the later runs’ non-formatting content into it, removes the now-empty donor runs, joins adjacent text pieces inside the survivor, and returns how many runs were removed.

**Call relations**: _merge_adjacent_runs calls this for each parent that contains Word runs. It relies on _canonical_rpr to decide which runs match and on _join_adjacent_text to clean up text nodes after content has been moved.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This tidies up a single Word run after other runs have been merged into it. It combines neighboring text nodes so the run contains one continuous piece of text where possible.

**Data flow**: It receives one run element, looks through its children, and whenever two neighboring children are both text elements, it joins their text into the first and removes the second. If the joined text starts or ends with a space, it marks the XML so those spaces are preserved.

**Call relations**: _merge_runs_in calls this after it has moved content from donor runs into an anchor run. It is the finishing step that turns a merged run from a pile of pieces into cleaner text.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This simplifies Word tracked changes by joining neighboring insertions or deletions from the same author. It reduces clutter without changing the actual changed text.

**Data flow**: It receives the path to word/document.xml. If the file exists, it reads and parses the XML while preserving blank text, finds paragraph and table-cell containers, and asks _coalesce_in to merge nearby insertion and deletion elements. If anything was merged, it writes the updated XML back and returns the number of removed tracked-change wrappers.

**Call relations**: unpack_docx calls this before run merging when tracked-change coalescing is enabled. It coordinates the document-wide search, while _coalesce_in handles each container and change type.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This looks inside one XML container for tracked insertions or deletions that belong together. It groups change elements by author before trying to merge them.

**Data flow**: It receives a paragraph or table-cell element and a change type, either insertion or deletion. It collects direct child elements of that type, groups them by the Word author attribute, sends each author group to _merge_change_run, and returns the total number of merged change elements.

**Call relations**: _coalesce_tracked_changes calls this for both insertions and deletions in each relevant container. It passes the real adjacency decision to _merge_change_run.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This joins a sequence of tracked-change elements when they are truly next to each other. It keeps the first change wrapper and moves the later wrappers’ contents into it.

**Data flow**: It receives a list of tracked-change XML elements from the same author. Starting with the first as the anchor, it checks each later element; if _changes_adjacent says it is separated only by whitespace or comments, it moves that element’s children into the anchor, preserves any trailing text, removes the later wrapper, and counts it as absorbed. If a later element is not adjacent, it becomes the new anchor.

**Call relations**: _coalesce_in calls this after grouping changes by author. It relies on _changes_adjacent to avoid merging changes that only look related but are actually separated by real document content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This answers a safety question: are two tracked-change elements next to each other in a way that makes merging them safe? It prevents the cleanup from crossing over real document content.

**Data flow**: It receives two XML elements, finds their shared parent and their positions among that parent’s children, and checks everything between them. It returns true only if the space between them is empty apart from whitespace or XML comments; otherwise it returns false.

**Call relations**: _merge_change_run calls this before combining two tracked-change elements. It acts as the guardrail that keeps tracked-change coalescing conservative and document-safe.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### PPTX repair and slide packaging
PowerPoint helpers unpack, clean, repair, manipulate, preview, and repack presentation packages at the XML-file level.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the surrounding `scripts` directory should be treated as an importable package. In everyday terms, it is like putting a label on a folder so the rest of the system knows, “you can look inside here for usable Python parts.”

Because it has no functions, classes, or settings, it does not perform any work directly. Nothing is calculated, loaded, or changed when reading this file beyond Python recognizing the package during imports. Without this file, depending on the Python version and packaging setup, code elsewhere in the project might have trouble importing script modules from this directory in a consistent way.

Its presence matters mostly for organization and compatibility. It helps keep the PowerPoint-related document skill code arranged as a clean package, even though the actual behavior lives in neighboring files.


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`io_transport` · `manual packaging or command-line tool run`

A PowerPoint `.pptx` file is really a ZIP archive full of folders and XML files. This script is the “pack it back up” tool: given a directory that contains those unpacked PowerPoint parts, it rebuilds the final `.pptx` archive. Before zipping, it walks through the XML and relationship files and removes formatting-only whitespace. That means it strips spaces and line breaks that exist only to make XML look pretty, not spaces that are part of visible slide text. This distinction matters because PowerPoint slide text often lives in DrawingML text tags, and removing whitespace there could change what the user sees. The script protects those text nodes while cleaning the rest.

The main flow is simple. It checks that the input is a directory and that the output name ends in `.pptx`. It copies the source into a temporary work area, cleans each `.xml` and `.rels` file in that copy, then writes every file into a compressed ZIP archive at the requested output path. Using a temporary copy is like working on a photocopy instead of the original: if cleaning fails, the source folder is not changed. When run directly from the command line, it accepts the source folder and output file as arguments, prints the result, and exits with an error code if packing failed.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing function. It validates the input folder and output filename, cleans XML files in a temporary copy, and writes the result as a compressed `.pptx` file.

**Data flow**: It receives a source directory path and an output file path. First it turns them into path objects, checks that the source is really a folder, and checks that the destination ends with `.pptx`. If either check fails, it returns no output path and an error message. Otherwise, it copies the whole source folder into a temporary working folder, sends every `.xml` and `.rels` file through `_condense_xml`, creates the output folder if needed, and writes all files into a ZIP archive. It returns the final output path and a success message.

**Call relations**: This is the top-level worker used by the command-line part of the script. During packing, it calls `_condense_xml` for each XML-like PowerPoint file before handing the cleaned files to the ZIP writer. It also relies on standard library tools for temporary folders, copying directories, path handling, and ZIP creation.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there for formatting. It deliberately avoids changing protected text elements so visible PowerPoint text is preserved.

**Data flow**: It receives the path to one XML or relationship file. It parses that file into an XML tree, walks through every node, and removes blank-only text or tail whitespace except in protected text tags. It also removes special parser nodes whose tag behaves like a callable object, such as processing instructions. Finally, it writes the cleaned XML back to the same file with a UTF-8 XML declaration. If parsing or writing fails, it prints an error message to standard error and raises the problem again so the caller knows packing did not complete safely.

**Call relations**: This function is called by `assemble_pptx` while preparing the temporary copy of the PowerPoint contents. It does not create the `.pptx` itself; instead, it prepares cleaner XML files that `assemble_pptx` later places into the final ZIP archive.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`entrypoint` · `post-generation repair step`

A `.pptx` file is really a ZIP archive full of XML files. This script opens that archive, checks for specific problems known to come from pptxgenjs, and rewrites the archive only if something needs fixing. The first problem is “phantom” slide master references: the package index may claim a slide master file exists even when it does not, which can make PowerPoint show a repair warning. The second problem is ZIP folder entries, which look harmless but break the packaging rules PowerPoint expects. The third problem is more subtle: text XML elements that begin or end with spaces need `xml:space="preserve"`, or PowerPoint may silently trim those spaces. That matters for things like indented code blocks or carefully aligned text. The script works like a careful archivist: it reads the package contents, checks the index against the real files, scans slide-related XML for vulnerable text, then writes a temporary cleaned copy. If writing succeeds, it replaces the original file. If there is nothing to fix, it leaves the file alone and says so.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function looks through slide-related XML files and protects text that starts or ends with a space or tab. It adds the XML marker that tells PowerPoint, “keep this whitespace exactly as written.”

**Data flow**: It receives a dictionary where each key is a file name inside the `.pptx` archive and each value is that file’s raw bytes. It only examines XML files that can contain presentation text, parses them, finds DrawingML text elements, and updates any text element with leading or trailing whitespace that is missing `xml:space="preserve"`. It returns a smaller dictionary containing only the changed files, plus a count of how many text elements were fixed.

**Call relations**: The main `repair` function calls this after reading the `.pptx` archive into memory. This helper uses XML parsing and XML writing from `lxml.etree` so that `repair` can later place the corrected XML back into the rebuilt archive.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a PowerPoint file. It checks whether the file exists, inspects the `.pptx` ZIP package, removes known bad entries, applies text whitespace fixes, and replaces the original file with a cleaned version when needed.

**Data flow**: It starts with a filename from the caller and turns it into a path. If the file is missing, it prints an error and returns `False`. Otherwise it opens the `.pptx` as a ZIP archive, reads real file entries, records which slide master files actually exist, detects folder entries, cleans false slide master references from `[Content_Types].xml`, and asks `_repair_whitespace_preservation` for text XML updates. If no issue is found, it prints that no repairs are needed and returns `True`. If repairs are needed, it writes a temporary ZIP without directory entries and with corrected XML, then moves that temporary file over the original and returns `True`.

**Call relations**: This function is called by the command-line block when someone runs the script directly. During its work it relies on `zipfile.ZipFile` to read and write the `.pptx` package, regular expression helpers to find and remove bad XML references, `_repair_whitespace_preservation` to protect whitespace in text, and `shutil.move` to safely replace the original file after the repaired copy is complete.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command-line execution`

A PowerPoint .pptx file is really a zipped folder full of XML files and resources such as images, charts, themes, and slide links. This script works directly with that folder structure. Without a tool like this, editing those internals by hand would be slow and easy to break, because each slide must be registered in several places and unused files can linger after edits.

The script has three main jobs. The clean command looks through relationship files, which are XML files that say “this part uses that part,” then deletes slide files and resources that are no longer referenced. It also removes stale entries from the content-types file so PowerPoint does not think deleted files still exist.

The add command either copies an existing slide or creates a blank slide connected to a chosen layout. It gives the new slide a safe number, adds the needed package records, and prints the XML line that still needs to be added to the presentation slide list.

The thumbnail command opens a PPTX, finds the slide order, renders visible slides through external tools, adds grey placeholders for hidden slides, and combines the images into one or more labeled JPEG grids. The file is both the user-facing command entry and the practical PowerPoint repair/editing logic behind it.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its root element, which is the top-level object the rest of the script can inspect or change. It is a small helper used whenever the script needs to understand a PowerPoint XML file.

**Data flow**: It receives a file path, asks the XML parser to load that file, and returns the root XML node. It does not change the file on disk.

**Call relations**: Many editing steps call this first so they can read relationship files, content-type records, or copied slide relationship files before deciding what to remove or add.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to a file. It is used after the script has changed PowerPoint bookkeeping files.

**Data flow**: It receives an XML root element and a destination path, turns the XML tree into UTF-8 bytes with an XML declaration, and overwrites the file at that path.

**Call relations**: Functions that remove stale links or register new slides call this after they have changed an XML tree in memory.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file inside the unpacked PowerPoint folder that is still mentioned by a relationship file. This is how the cleaner knows what is still in use.

**Data flow**: It receives the unpacked PPTX directory, searches all .rels files under it, reads each relationship target, resolves it to a safe path inside the package, and returns a set of referenced relative paths.

**Call relations**: run_clean calls this during each cleanup pass, then gives the result to the resource-removal step so only unreferenced files are deleted.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds which slide XML files are actually part of the presentation’s slide list. This prevents the cleaner from keeping abandoned slide files that still sit in the folder but are no longer shown.

**Data flow**: It reads presentation.xml and its relationship file, matches slide relationship IDs to slide filenames, then returns the filenames that appear in the active slide list. If the expected files are missing, it returns an empty set.

**Call relations**: run_clean calls this before deleting orphan slides, so _remove_orphan_slides can tell the difference between real slides and leftover slide files.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special temporary trash folder inside the unpacked PowerPoint directory. This is a final sweep for files that were intentionally parked for removal.

**Data flow**: It receives the unpacked directory, checks for the [trash] folder, deletes files inside it, removes the folder, and returns a list of deleted relative paths.

**Call relations**: run_clean calls this after removing orphan slides, adding its results to the same deletion report shown to the user.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that are not in the active presentation slide list. It also removes their companion relationship files and stale presentation links.

**Data flow**: It receives the unpacked directory and the set of active slide names. It scans ppt/slides, deletes slide XML files not in that set, deletes matching .rels files, updates presentation.xml.rels if needed, and returns deleted paths.

**Call relations**: run_clean calls this early because unused slides can point to images or charts; removing them first lets later cleanup passes remove resources that only those slides used.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, embedded objects, charts, diagrams, themes, notes, and related relationship files. These are the loose parts left behind when slides or objects are removed.

**Data flow**: It receives the unpacked directory and a set of currently referenced paths. It checks known PowerPoint resource folders, deletes files not in the referenced set, removes relationship files whose parent files are gone, and returns deleted paths.

**Call relations**: run_clean calls this repeatedly after collecting references. Because deleting one file can make another file unused, the cleaner loops until this function has nothing more to remove.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes content-type records for files that were deleted. Content types are PowerPoint’s index of what kind of file each package part is.

**Data flow**: It receives the unpacked directory and a list of removed package paths. It opens [Content_Types].xml, removes Override entries for deleted parts, and writes the file back only if something changed.

**Call relations**: run_clean calls this at the end, after all deletion passes are finished, so PowerPoint’s package index matches the files that remain.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Performs the full cleanup operation on an unpacked PPTX directory. It removes inactive slides, trash files, unused resources, and stale package records.

**Data flow**: It receives the unpacked directory, gathers active slide names, deletes orphan slides and trash, repeatedly collects references and removes unused resources, updates content types, and returns the full list of deleted files.

**Call relations**: _cmd_clean calls this after validating the user’s path. It coordinates the lower-level cleanup helpers and hands the deletion list back for printing.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as slide12.xml. This avoids overwriting an existing slide.

**Data flow**: It receives the slides folder, scans filenames that look like slide<number>.xml, finds the highest number, and returns one more. If there are no slides, it returns 1.

**Call relations**: Both slide-creation paths call this before writing a new slide file.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to [Content_Types].xml so the PPTX package says the file is a PowerPoint slide. Without this, PowerPoint may not recognize the new file correctly.

**Data flow**: It receives the unpacked directory and the new slide filename. It reads the content-types XML, checks whether the slide is already registered, adds an Override entry if missing, and writes the XML back.

**Call relations**: _create_from_layout and _clone_existing call this after creating the slide file, before the new slide is fully known to the package.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the presentation to the new slide. This gives the new slide a relationship ID, which is the link name used elsewhere in the presentation XML.

**Data flow**: It receives the unpacked directory and slide filename. It reads presentation.xml.rels, returns an existing relationship ID if one already points to that slide, or creates a new rId with the next number and returns it.

**Call relations**: The two add helpers call this after creating a slide. They use the returned relationship ID in the instruction printed for updating the presentation slide list.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for presentation.xml. This is separate from the filename number and from the relationship ID.

**Data flow**: It reads presentation.xml, finds existing slide ID numbers, and returns one more than the largest. If none are found, it starts at 256, which is the usual PowerPoint starting range.

**Call relations**: _create_from_layout and _clone_existing call this so they can print a correct slide-list entry for the user to insert.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that uses an existing slide layout. A layout is a template-like PowerPoint part that defines placeholders and styling.

**Data flow**: It receives the unpacked directory and a layout filename. It checks that the layout exists, creates a new slide XML file and relationship file pointing to the layout, registers the slide in package records, and prints the XML line needed to add it to the slide list. If the layout is missing, it prints an error and exits.

**Call relations**: run_add calls this when the user’s source name looks like a slide layout file. It relies on numbering and registration helpers to make the new package parts consistent.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Copies an existing slide to make a new slide. It also avoids copying the old slide’s speaker-notes relationship, so the duplicate does not point at the same notes slide.

**Data flow**: It receives the unpacked directory and source slide filename. It checks the source exists, copies the slide XML and relationship file if present, removes notes-slide relationships from the copy, registers the new slide, and prints the XML line needed for the slide list. If the source is missing, it exits with an error.

**Call relations**: run_add calls this for normal slide filenames. It uses the XML helpers when cleaning copied relationships and the registration helpers to connect the copied slide to the presentation.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses the right way to add a slide based on the source name. It is the main add operation behind the command-line subcommand.

**Data flow**: It receives the unpacked directory and a source string. If the source looks like slideLayout*.xml, it creates a blank slide from that layout; otherwise, it clones an existing slide.

**Call relations**: _cmd_add calls this after validating the unpacked directory. It then delegates to either _create_from_layout or _clone_existing.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a PPTX file and finds the slides in presentation order, including whether each slide is hidden. This lets thumbnails match what a person sees in PowerPoint’s slide list.

**Data flow**: It receives a .pptx path, opens it as a zip file, reads presentation relationships and presentation.xml, matches relationship IDs to slide filenames, and returns ordered records with slide names and hidden flags.

**Call relations**: run_thumbnail calls this first, before rendering, so later steps can pair rendered images with the correct slide names and hidden-slide placeholders.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns visible slides into JPEG images by using external programs. It first asks LibreOffice to convert the PPTX to PDF, then asks pdftoppm to convert the PDF pages to JPEGs.

**Data flow**: It receives the PPTX path and a temporary work directory. It runs the conversion commands, checks that they succeeded, and returns the generated slide image paths in order. If conversion fails, it raises an error.

**Call relations**: run_thumbnail calls this after reading slide order. The returned image files are later matched with slide names and assembled into grids.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a grey crossed-out image to stand in for a hidden slide. Hidden slides are part of the deck but are not rendered by the normal visible-slide conversion.

**Data flow**: It receives image dimensions, creates a blank grey image, draws two diagonal lines across it, and returns the image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden, so the final thumbnail grid still shows that the hidden slide exists.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (Draw, new).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches each slide record to the image that should represent it. Visible slides get rendered JPEGs; hidden slides get generated placeholders.

**Data flow**: It receives the slide-order records, rendered image paths, and a work directory. It uses the first rendered image size for placeholder dimensions when possible, walks the slide order, creates hidden placeholders when needed, and returns pairs of image path and label.

**Call relations**: run_thumbnail calls this after rendering. It bridges the logical slide list from _extract_slide_order with the image files from _render_slide_images.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one labeled contact sheet image from slide thumbnail items. It is like laying printed slide previews into rows and columns on a white page.

**Data flow**: It receives image-and-label pairs, the number of columns, and the desired cell width. It calculates cell sizes, creates a white canvas, writes each label, resizes each slide image to fit, pastes it into place, draws an outline, and returns the finished image.

**Call relations**: run_thumbnail calls this once for each chunk of slides that fits in a grid. The caller then saves the returned image as a JPEG.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (Draw, load_default, new, open).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG thumbnail grids for a PowerPoint deck. It is the main thumbnail operation behind the command-line subcommand.

**Data flow**: It receives a PPTX path, output prefix, and column count. It reads slide order, creates a temporary workspace, renders slides, builds image-label pairs, splits them into grid-sized chunks, saves each grid JPEG, and returns the saved filenames. If no slides are found, it prints an error and exits.

**Call relations**: _cmd_thumbnail calls this after checking the input file and column limit. It coordinates all thumbnail helpers from extraction through rendering, pairing, grid composition, and saving.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the clean subcommand. It checks the user’s input path and prints a human-readable cleanup report.

**Data flow**: It receives parsed command-line arguments, turns the unpacked directory string into a path, exits with an error if it does not exist, calls run_clean, and prints either the removed files or a no-op message.

**Call relations**: The argument parser attaches this function to the clean subcommand. When the script is run with clean, the main block calls it through the parsed arguments.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the add subcommand. It validates the unpacked directory and starts the slide-add operation.

**Data flow**: It receives parsed arguments, converts the directory to a path, exits if the path is missing, and passes the directory and source name to run_add.

**Call relations**: build_parser connects this to the add subcommand. It is the thin command-line wrapper around the add workflow.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the thumbnail subcommand. It checks the PPTX input, caps the column count, runs thumbnail creation, and prints the output files.

**Data flow**: It receives parsed arguments, validates that the input exists and has a .pptx extension, limits columns to the configured maximum, calls run_thumbnail, and prints saved grid paths. If anything fails, it prints an error and exits.

**Call relations**: build_parser connects this to the thumbnail subcommand. It is the user-facing wrapper around the rendering and grid-building workflow.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface: the available subcommands, their arguments, help text, and which function runs for each command.

**Data flow**: It creates an argument parser, adds clean, add, and thumbnail subcommands, assigns each subcommand its expected inputs and callback function, and returns the parser.

**Call relations**: The main block calls this when the script starts. After parsing the user’s command, the selected callback function is invoked.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual preprocessing / command-line run`

A PowerPoint .pptx file is really a ZIP archive: a bundle of many smaller files, most of them XML, which is a text format for structured data. This script opens that bundle and lays its contents out in a normal directory so someone can inspect or edit the presentation internals directly.

The main job starts by checking that the input file exists and really has a .pptx name. It then creates the output folder if needed, unzips the presentation into it, and searches the extracted files for XML documents and relationship files, which use the .rels extension. For each of those files, it first tries to pretty-print the XML. That means adding consistent line breaks and indentation, like tidying a messy outline so its structure is visible. After that, it scans the same files for “smart quotes” and ‘curly apostrophes’ and replaces them with XML entity codes, which are safer in XML text.

The script is deliberately forgiving during cleanup: if one XML file cannot be parsed or rewritten, the helper quietly skips it instead of stopping the whole unpacking job. At the end, it reports how many XML-like files it processed, or returns a clear error message if the source file was missing, had the wrong extension, or was not a valid ZIP archive.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker for unpacking a PowerPoint file into a directory. Someone would use it when they need the hidden XML files inside a .pptx to be available as normal, readable files on disk.

**Data flow**: It receives a path to a .pptx file and a destination folder. It checks the source path, creates the destination folder, extracts the ZIP contents there, finds all .xml and .rels files, then asks helper functions to format the XML and escape curly quotes. It returns either an ExtractionResult with the count of processed XML-like files plus a success message, or None plus an error message if the file is missing, not named .pptx, or not a valid ZIP archive.

**Call relations**: This function is the center of the script. When the script is run from the command line, the parsed arguments are passed here. During its work it calls _prettify_xml first to make each XML file easier to read, then _escape_smart_quotes to make quote characters safer for XML editing.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper makes one XML file easier to read by parsing it and writing it back with consistent indentation. It is like taking a long, cramped checklist and spacing it into a clear outline.

**Data flow**: It receives the path to one extracted XML or relationship file. It tries to parse the file as XML, add two-space indentation, and write the cleaned-up version back to the same file using UTF-8 text encoding. If anything goes wrong, it leaves the file as-is and does not report an error.

**Call relations**: extract_pptx calls this helper for every .xml and .rels file it finds after unzipping the presentation. It finishes before _escape_smart_quotes runs, so the file is first made readable and then checked for quote characters.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks and apostrophes with XML entity codes. That keeps those characters explicit and safer when the extracted XML is edited or processed by tools.

**Data flow**: It receives the path to one extracted XML or relationship file. It reads the file as UTF-8 text, checks whether any smart quote characters are present, replaces each one with its matching entity such as &#x201C;, and writes the changed text back to the same file. If there are no smart quotes, it does nothing; if reading or writing fails, it quietly leaves the file unchanged.

**Call relations**: extract_pptx calls this helper for every XML-like file after _prettify_xml has had a chance to format it. It is the final cleanup step before extract_pptx reports how many files were processed.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### XLSX recalculation support
Excel helpers provide headless LibreOffice integration and use it to recalculate workbooks and surface remaining formula errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. In Python projects, a file with this name is commonly used as a signpost that says, “this folder can be treated as a package.” A package is simply a folder of Python code that can be imported by other Python code.

Here, the folder belongs to the Office XLSX document skill, under its `scripts` area. Even though this file does not define any functions, classes, or settings, it can still matter. Some tools, import systems, or packaging steps expect `__init__.py` to be present before they will recognize a directory as part of the Python module tree. Without it, code that tries to import modules from this folder might fail in some environments, or packaging tools might skip the folder.

An everyday analogy: this file is like a blank label on a filing cabinet drawer. The label does not contain the documents, but it tells the system that the drawer belongs to the organized filing system.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`io_transport` · `called during document processing when scripts need LibreOffice`

This file is a thin convenience layer around LibreOffice's command-line program, `soffice`. Other spreadsheet-related scripts can use it when they need LibreOffice to open, convert, or process files in the background. Without these helpers, each script would have to repeat the same setup details, and small differences could cause LibreOffice to behave differently on different machines.

The main issue it solves is headless operation. “Headless” means running an application without showing its normal graphical window, like asking a shop to prepare an order in the back room instead of at the front counter. LibreOffice sometimes still expects a display system even when used from automation. The helper `soffice_env` copies the current process environment and adds a setting that tells LibreOffice to use a non-windowed visual backend.

The file also provides `macro_dir`, which returns the standard folder where LibreOffice Basic macros live. That folder differs between macOS and Linux, so the helper chooses the right path for the current operating system.

Finally, `run_soffice` builds the actual `soffice` command, runs it, captures its output, and optionally stops it if it takes too long. This gives the rest of the project one predictable way to call LibreOffice.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Creates the environment settings used when launching LibreOffice in the background. Its key job is to tell LibreOffice to use a headless-friendly display mode instead of trying to open a normal graphical interface.

**Data flow**: It starts with the current process environment variables, makes a copy, then adds or replaces `SAL_USE_VCLPLUGIN` with `svp`. It returns that modified environment dictionary, leaving the original environment untouched.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls `soffice_env` to get the right launch settings. The returned environment is then handed to `subprocess.run`, so the external `soffice` program starts with these background-friendly settings.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Finds the standard LibreOffice macro folder for the current operating system. This is useful for scripts that need to install, read, or refer to LibreOffice macros.

**Data flow**: It asks the operating system name, chooses the matching template path for macOS or Linux, expands `~` into the user's home directory, and turns the result into a `Path` object. If the operating system is not listed, it falls back to the Linux-style location.

**Call relations**: This helper stands on its own for code that needs the macro folder. It relies on `platform.system` to identify the operating system and `pathlib.Path` to return a path object that other file-handling code can use cleanly.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program with the given arguments and returns the completed result. It centralizes how the project invokes `soffice`, including output capture, text decoding, environment setup, and optional timeout.

**Data flow**: It receives a list of command arguments and an optional timeout in seconds. It prefixes those arguments with the `soffice` program name, gets the prepared environment from `soffice_env`, and runs the command while capturing standard output and standard error as text. It returns Python's completed-process result, which includes details such as the exit code and captured output.

**Call relations**: Other document-processing scripts can call `run_soffice` whenever they need LibreOffice to do work. Inside, it first asks `soffice_env` for safe background-running settings, then hands the full command to `subprocess.run`, which performs the actual external program call.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`orchestration` · `command-line document processing`

Excel files can contain formulas whose saved results are out of date, especially after automated editing. This file acts like a quality-control step: it asks LibreOffice to open the workbook without showing a window, recalculate every formula, save the result, and then inspect the workbook for common spreadsheet errors such as #REF! or #DIV/0!. Without this step, later tools might read old formula results and think the spreadsheet is correct when it is not.

The script uses a small LibreOffice macro, which is like a tiny instruction card LibreOffice can run inside a spreadsheet. The macro tells LibreOffice to calculate everything, store the file, and close it. Before running LibreOffice, the script makes sure that macro exists in LibreOffice's macro folder.

There is one extra safeguard for Excel table styling. LibreOffice can sometimes drop or alter table style XML inside .xlsx files. Because an .xlsx file is really a zipped bundle of XML files, the script takes a snapshot of table style snippets before recalculation and patches them back afterward.

Finally, it uses openpyxl, a Python library for reading Excel files, to scan saved cell values for known error strings and count formulas. The result is returned as a JSON-friendly dictionary, and the script can also be run directly from the command line.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: This function makes sure LibreOffice has the macro needed to recalculate and save the spreadsheet. It prevents the main recalculation step from failing just because LibreOffice has not yet been prepared.

**Data flow**: It looks for the expected macro file in LibreOffice's macro directory. If the file already exists and contains the needed macro name, it reports success. If the macro folder is missing, it briefly starts LibreOffice in headless mode to create the user setup, then writes the macro file. It returns true if the macro is ready, or false if writing it failed.

**Call relations**: The main recalc function calls this before trying to open the workbook through LibreOffice. To do its job, it asks _soffice.macro_dir where the macro should live, uses _soffice.soffice_env to run LibreOffice with the right environment, and may call subprocess.run to initialize LibreOffice's profile.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: This function saves copies of Excel table style snippets before LibreOffice touches the file. It exists because LibreOffice can sometimes change or remove those snippets while recalculating.

**Data flow**: It receives the path to an .xlsx file, opens it as a zip archive, and looks through the internal XML files for table definitions. When it finds a table style element, it stores that raw XML by filename in a dictionary. The output is that dictionary of style snippets.

**Call relations**: The recalc function calls this just before running LibreOffice. The saved snippets are later passed to _restore_table_styles so the workbook can keep the same table appearance metadata after recalculation.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: This function inserts or replaces a table style element inside one table XML file. It is the small repair tool used when restoring table styles after LibreOffice has saved the workbook.

**Data flow**: It takes the raw XML bytes for a table file and the saved style XML bytes. If the table already has a style element, it replaces it. If not, it inserts the saved style just before the closing table tag. It returns the corrected XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file that had a saved style. This function does the focused patching work while _restore_table_styles handles reading and rewriting the larger .xlsx zip archive.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: This function puts previously saved Excel table styles back into the workbook after LibreOffice has recalculated and saved it. It protects formatting-related metadata that LibreOffice may disturb.

**Data flow**: It receives the workbook path and a dictionary of saved style snippets. If there are no saved styles, it does nothing. Otherwise, it creates a temporary zip file, copies every file from the workbook into it, patches matching table XML files with _patch_table_style, and then replaces the original workbook with the repaired one. If something goes wrong, it removes the temporary file when possible.

**Call relations**: The recalc function calls this after LibreOffice finishes successfully. It relies on _patch_table_style for the actual XML repair, uses zipfile.ZipFile to read and write the .xlsx package, and uses shutil.move to swap the repaired file into place.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: This function checks the recalculated workbook for common Excel error values. It answers the practical question: after recalculation, are any cells visibly broken?

**Data flow**: It opens the workbook with saved formula results rather than formula text, then walks through every worksheet, row, and cell. If a cell contains text matching a known Excel error such as #REF! or #DIV/0!, it records the sheet name and cell address. It returns a dictionary mapping each error type to the places where it was found.

**Call relations**: The recalc function calls this after LibreOffice has saved the recalculated workbook and table styles have been restored. It uses openpyxl.load_workbook to read the spreadsheet contents.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: This function counts how many formulas are present in the workbook. The count gives context for the recalculation report, such as whether a clean result came from a formula-heavy workbook or from a file with no formulas at all.

**Data flow**: It opens the workbook while preserving formula text, then walks through every cell. Whenever a cell contains a string that starts with an equals sign, it counts it as a formula. It closes the workbook and returns the final number.

**Call relations**: The recalc function calls this near the end, after scanning for errors, to include the total formula count in the returned summary. It uses openpyxl.load_workbook to inspect the workbook.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: This is the main work function for the script. Given an Excel filename, it recalculates the workbook through LibreOffice and returns a clear machine-readable report about success, timeout, setup failure, or remaining formula errors.

**Data flow**: It receives a filename and an optional timeout. First it checks that the file exists. Then it ensures the LibreOffice macro is installed, snapshots table styles, runs LibreOffice headlessly with the recalculation macro, restores table styles, scans for spreadsheet errors, counts formulas, and builds a result dictionary. The output is either an error dictionary or a summary with status, total errors, total formulas, and sample error locations.

**Call relations**: main calls this when the script is run from the command line. Inside, recalc coordinates all helper functions: _ensure_macro prepares LibreOffice, _snapshot_table_styles and _restore_table_styles protect table metadata, _scan_errors checks the result, and _count_formulas adds context. It hands the actual LibreOffice process launch to _soffice.run_soffice.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: This is the command-line entry point. It reads the user's arguments, runs recalculation, and prints the result as formatted JSON.

**Data flow**: It reads sys.argv to get the Excel filename and optional timeout. If no filename is provided, it prints a usage message and exits with an error code. Otherwise, it calls recalc, converts the returned dictionary to JSON text, and prints it.

**Call relations**: This function is called only when the file is executed as a script. It is the thin outer wrapper around recalc, leaving the real spreadsheet work to that function and using json.dumps to make the result easy for people or other programs to read.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).


### PDF forms and previews
PDF helpers inspect native form fields, place answers on non-form layouts, and render pages to images for preview or downstream processing.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command-line use for PDF form detection, extraction, and filling`

PDF forms are not just pictures of boxes. A well-made fillable PDF contains hidden form fields with names, types, allowed values, and page locations. This file reads that hidden structure so the rest of the system can treat the PDF like a form instead of guessing where to type.

It supports three jobs. First, it can check whether a PDF has fillable fields. Second, it can extract the form layout into JSON: field name, kind, page number, rectangle position, and possible values for checkboxes, radio buttons, and choice lists. Third, it can take a JSON file of chosen values and write a new PDF with those values filled in.

The file uses pypdf, a Python library for reading and writing PDF files. It knows about AcroForm, the standard PDF form system, and also checks for “orphaned widgets,” which are visible form controls that are present on pages even when the main form dictionary is incomplete. That fallback matters because real-world PDFs are often messy.

A helpful mental model is a clipboard with labeled blanks. This file finds the labels, records where each blank sits, checks that answers are valid, and then asks pypdf to write the answers into the right blanks.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line execution for PDF extraction, preview, or filling`

Some PDFs look like forms but do not contain actual form fields that software can fill in. This file helps with that problem by treating the PDF like a printed page: it finds text, long horizontal lines, and small square boxes, then uses a separate JSON description to place text in the right spots. Think of it like putting transparent sticky notes on top of a paper form.

The tool has three commands. The extract command opens a PDF and records each page’s size, words, likely row boundaries, long ruling lines, and small boxes that may be checkboxes. The preview command draws colored rectangles on an image of a page, so a human can quickly see whether the planned field locations look right before changing the PDF. The fill command reads a field JSON file, checks that text boxes are not obviously too small or overlapping, converts coordinates into the PDF’s coordinate system, and adds FreeText annotations using pypdf.

One important detail is that images and PDFs count vertical position differently. Images usually count y positions from the top down, while PDF annotations use a bottom-up coordinate system. CoordMapper is the translator that keeps text from landing upside down or in the wrong place.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: This method converts a rectangle from the field description into the rectangle format needed for a PDF annotation. It is especially important when the field coordinates came from an image preview rather than directly from the PDF.

**Data flow**: It receives a box as four numbers: left, top, right, and bottom. It looks at the page size and the declared coordinate system, scales the box if it came from an image, flips the vertical direction to match PDF coordinates, and returns a four-number annotation rectangle ready for pypdf.

**Call relations**: During the fill command, _validate_and_fill creates a CoordMapper for each field’s page and asks this method to translate the content area before creating the PDF text annotation.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: This function scans one PDF page and turns its visible layout into structured information. It finds page size, words, long horizontal rules, and small square rectangles that may be checkboxes.

**Data flow**: It receives a pdfplumber page and a page number. It reads the page’s drawn lines, rectangles, and extracted words; filters them using simple size and shape rules; rounds positions to one decimal place; and returns a PageLayout object containing the discovered layout clues.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. After this function builds the basic page layout, _extract_all_pages sends that layout to _compute_row_ranges to infer rows between horizontal lines.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: This function guesses row areas on a page by looking at the horizontal rules found earlier. It helps turn separate line positions into usable bands, like rows in a table.

**Data flow**: It receives a PageLayout that already contains horizontal rule positions. It sorts the rule heights from top to bottom, pairs neighboring lines, calculates the space between each pair, and appends those row ranges back onto the same PageLayout object.

**Call relations**: _extract_all_pages calls this immediately after _extract_page. It enriches each page’s extracted layout before the page is saved into the final extraction result.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: This function scans an entire PDF, page by page. It is the main workhorse behind the extract command.

**Data flow**: It receives a PDF file path. It opens the PDF with pdfplumber, loops through every page, builds a PageLayout with _extract_page, adds row ranges with _compute_row_ranges, collects all page layouts, and returns the full list.

**Call relations**: cmd_extract calls this after checking command-line arguments. It coordinates the per-page scanning functions and hands the finished page layouts back to cmd_extract for JSON output.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: This function converts PageLayout objects into plain dictionaries that can be written as JSON. JSON needs simple values such as lists, numbers, strings, and dictionaries, not Python dataclass objects.

**Data flow**: It receives a list of PageLayout objects. For each page, it copies the page number, size, text elements, horizontal rules, tick boxes, and row ranges into a regular dictionary, then returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after _extract_all_pages finishes scanning the PDF. Its output is then passed to json.dumps so the extracted layout can be saved to disk.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: This function reads a field plan and writes text annotations into a PDF, while checking for common layout mistakes first. It prevents obvious bad output, such as text that is too tall for its box or fields that overlap.

**Data flow**: It receives paths for the input PDF, the field JSON file, and the output PDF. It reads the JSON, opens the PDF, records page sizes, checks each field’s content area and overlap against earlier fields, converts each field rectangle with CoordMapper, creates a FreeText annotation, and adds it to the PDF writer. If errors are found, it prints them and exits; otherwise it writes the new PDF file and prints a summary.

**Call relations**: cmd_fill calls this after validating the fill command’s arguments. Inside the filling loop, it uses _rects_overlap for safety checks and CoordMapper.to_annotation_rect before handing the final rectangle to pypdf’s FreeText annotation creation.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: This small helper answers a simple question: do two rectangular areas touch or cover the same space? It is used to catch field placements that would collide on the page.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges and returns true if they overlap, or false if one is fully to the side or above the other.

**Call relations**: _validate_and_fill calls this while reviewing each new field against areas that were already placed on the same page. Its result decides whether an overlap error should be reported.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: This is the command-line action for turning a PDF’s visible layout into a JSON file. Someone uses it when they need a map of where text, lines, and boxes appear before designing fields to fill.

**Data flow**: It receives the command arguments after the word extract. It checks that there are exactly two paths, scans the input PDF with _extract_all_pages, converts the results with _pages_to_dict, writes the JSON output file, and prints a count of the discovered items.

**Call relations**: main dispatches to this function when the user runs layout.py extract. This function then drives the extraction helpers and is responsible for saving their result in a human-readable JSON file.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: This is the command-line action for drawing planned form field boxes onto an image of a PDF page. It gives a quick visual check before writing annotations into the real PDF.

**Data flow**: It receives a page number, a field JSON path, an input image path, and an output image path. It loads the field definitions, opens the image, draws red rectangles for content areas and blue rectangles for label boxes on the requested page, saves the marked-up image, and prints how many fields were highlighted.

**Call relations**: main dispatches to this function when the user runs layout.py preview. It does not change the PDF; it only creates an image preview that helps a human confirm the field coordinates.

*Call graph*: 5 external calls (Draw, open, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: This is the command-line action for producing a new PDF with text placed into the planned fields. It is the user-facing wrapper around the actual validation and annotation work.

**Data flow**: It receives the command arguments after the word fill. It checks that the user provided an input PDF, a field JSON file, and an output PDF path, then passes those three paths to _validate_and_fill.

**Call relations**: main dispatches to this function when the user runs layout.py fill. It hands off nearly all real work to _validate_and_fill after making sure the command was shaped correctly.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: This is the script’s front door. It reads the command name from the terminal and sends control to the matching command function.

**Data flow**: It reads sys.argv, which contains the words typed by the user. If there is no valid subcommand, it prints a usage message and exits; otherwise it calls the selected function from SUBCOMMANDS with the remaining arguments.

**Call relations**: When this file is run directly with python layout.py, the bottom of the file calls main. main then dispatches to cmd_extract, cmd_preview, or cmd_fill depending on what the user requested.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command-line run`

PDF files are good for documents, but many tools that inspect or display content work more easily with images. This file solves that gap by rendering each page of a PDF into a PNG file. Think of it like taking a clear screenshot of every page in a document and saving those screenshots in order.

The main work happens in `render`. It first makes sure the output folder exists. Then it asks `pdf2image`, an outside library that converts PDF pages into image objects, to read the PDF at a fixed quality level of 200 DPI. DPI means “dots per inch”; higher values usually mean sharper, larger images.

For each page image, the file checks whether it is wider or taller than 1000 pixels. If it is too large, it shrinks the image while keeping the same shape, so the page does not look stretched. Each page is then saved as `page_1.png`, `page_2.png`, and so on. The script prints a short progress message for every page and a final summary at the end.

The `main` function makes this usable from a terminal. It expects exactly two arguments: the input PDF path and the output folder. If the user gives the wrong number of arguments, it prints the correct usage and exits with an error.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: This function converts every page of a PDF into a PNG image file. Someone would use it when they need page-by-page image versions of a PDF, for example for display, inspection, or later image-based processing.

**Data flow**: It receives a PDF file path and an output directory path. It creates the output directory if needed, reads the PDF through `pdf2image.convert_from_path`, optionally shrinks any oversized page image, then writes each page to disk as a numbered PNG file. Its visible outputs are the saved image files and progress messages printed to the terminal; it does not return a value.

**Call relations**: The command-line `main` function calls `render` after checking the user supplied the right arguments. Inside the work, `render` relies on `pathlib.Path` to create and build filesystem paths, and on `pdf2image.convert_from_path` to do the actual PDF-to-image conversion before it saves the results.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: This function is the command-line front door for the script. It checks whether the user provided the two required pieces of information: the PDF to read and the folder where images should be written.

**Data flow**: It reads the command-line arguments from `sys.argv`. If the argument count is wrong, it prints a usage message and exits with an error code. If the arguments are correct, it passes the input PDF path and output directory path to `render`; it does not produce a return value of its own.

**Call relations**: When this file is run directly, Python calls `main`. `main` either stops early through `sys.exit` when the command is malformed, or hands off to `render` to perform the actual page rendering.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
