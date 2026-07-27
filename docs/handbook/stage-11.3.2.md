# Word DOCX unpacking, packing, comments, and tracked changes  `stage-11.3.2`

This stage is Word-specific behind-the-scenes support for working with DOCX files. A DOCX file looks like one document, but it is really a zipped package of many XML files, which are text files that describe the document’s content and settings. The unpack script opens that package into a folder and tidies the main document XML so later tools can read, edit, or compare it without extra noise.

Once unpacked, the comment script can add a Word comment. It does not just write one note. It updates several hidden XML parts and links between them, because Word needs those records to know where the comment belongs and how to display it.

The pack script then reverses the process. It cleans unnecessary spacing in the XML and zips the folder back into a normal DOCX file Word can open.

The accept_changes script handles another Word task: tracked changes. It runs LibreOffice in the background, accepts all edits, and saves a clean copy without showing a window.

## Files in this stage

### Tracked change acceptance
Headless LibreOffice automation produces a DOCX copy with all tracked changes accepted.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `on-demand document cleanup`

This file solves a practical document-cleanup problem: Word documents may contain tracked edits, and later steps often need a clean version where those edits are accepted. Instead of trying to edit the DOCX file format directly, the script asks LibreOffice to do the job, much like asking a word processor to open the file, click “Accept All Changes,” save, and close.

The script first checks that the input exists and is a DOCX file. It then copies the input to the requested output path, so the original is not changed. Next, it makes sure LibreOffice has a small Basic macro installed in a temporary user profile. A macro is a short script run inside LibreOffice; here it performs the built-in “Accept All Tracked Changes” command, saves the document, and closes it.

LibreOffice is launched with special settings for server use: no visible interface, a temporary profile, and a simple display backend. One important behavior is that LibreOffice may hang even after successfully saving the file. Because of that, a timeout while running the macro is treated as success. Without this script, the system would need a person or a much more fragile DOCX editor to clean tracked changes.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This prepares the environment variables used when starting LibreOffice. It tells LibreOffice to use a simple non-graphical display mode, which is better suited for background processing.

**Data flow**: It starts with a copy of the current process environment. It adds or replaces the LibreOffice display setting, then returns that adjusted environment for subprocess calls.

**Call relations**: The macro setup step and the main document-processing step both call this before launching LibreOffice. It supplies the safe background-running settings that those subprocess launches need.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This builds the command-line option that tells LibreOffice which temporary user profile to use. A profile is LibreOffice’s private settings folder, and this script uses one dedicated to the macro it installs.

**Data flow**: It reads the fixed temporary profile path from the file-level constant. It formats that path as a LibreOffice command-line argument and returns the resulting string.

**Call relations**: The macro installer and the main LibreOffice run both use this so they talk to the same temporary profile. That matters because the macro is installed into that profile before it is executed.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure LibreOffice has the Basic macro needed to accept tracked changes. If the macro is already installed, it leaves it alone; otherwise, it creates the needed folder and writes the macro file.

**Data flow**: It checks the expected macro file. If the file exists and contains the macro name, it reports success. If not, it may start LibreOffice briefly to initialize the temporary profile, creates the macro folder if needed, writes the macro XML, and returns success.

**Call relations**: The main accept_tracked_changes function calls this after copying the document and before running LibreOffice on it. Inside, this function uses _profile_arg and _soffice_env to start LibreOffice with the same profile and background settings that the later macro execution will use.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main worker function: it creates a cleaned copy of a DOCX file by accepting all tracked changes through LibreOffice. It returns a message explaining either success or the reason it could not proceed.

**Data flow**: It receives an input path and an output path. It checks that the input exists and has a .docx extension, creates the output folder if needed, and copies the input file to the output location. It then ensures the LibreOffice macro is installed, starts LibreOffice in headless mode to run that macro on the copied file, and returns a success or error message. The original file is not modified; the output file is the one changed.

**Call relations**: This is the function used by the command-line script at the bottom of the file. During its work it calls _ensure_macro to prepare LibreOffice, then uses _profile_arg and _soffice_env when launching LibreOffice to run the macro. It also relies on file-copying and subprocess execution from the standard library to move the document and invoke the external office program.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### DOCX XML editing workflow
DOCX archives are unpacked for XML editing, updated with Word comment metadata, and packed back into a clean document.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual document unpacking and preprocessing`

A .docx file is really a ZIP archive full of XML files. This script opens that archive, extracts its contents, and rewrites the XML in a friendlier form. Without this step, a person or tool trying to inspect or edit a Word document would face compressed files and hard-to-read XML with many tiny, distracting fragments.

The main flow is like unpacking a suitcase and then neatly folding the clothes. First, `unpack_docx` checks that the input exists and looks like a .docx file. It creates the output folder, unzips the document there, and formats every XML-like file with indentation so it is easier to read.

Then it focuses on `word/document.xml`, the file that contains the main body of the Word document. If enabled, it combines neighboring tracked changes by the same author, so several adjacent insertions or deletions become one larger change. If enabled, it also merges neighboring Word “runs” that have the same formatting. A run is a small piece of text with its own styling; Word often splits text into many runs for reasons that are not meaningful to humans. Finally, it replaces curly quote characters with XML character entities, making them explicit and stable in text files.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker for the script. It validates the input .docx file, extracts it into a folder, makes the XML readable, optionally cleans up tracked changes and text runs, and returns both a result object and a human-readable message.

**Data flow**: It receives the source file path, destination folder path, and two yes-or-no options for cleanup. It checks the file, creates the destination folder, unzips the .docx contents, finds XML and relationship files, formats them, edits `word/document.xml` if present, replaces curly quotes, and then returns an `UnpackResult` plus a summary string. If the file is missing, has the wrong extension, or is not a valid ZIP archive, it returns no result and an error message.

**Call relations**: This function is the hub of the file. The command-line block calls it after reading user arguments. During its work, it hands each XML file to `_indent_xml`, sends the main document XML to `_coalesce_tracked_changes` and `_merge_adjacent_runs` when those options are enabled, and finally sends XML files to `_replace_curly_quotes` before reporting what changed.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This function rewrites one XML file with clean indentation. It makes the file easier for people and text-based tools to read.

**Data flow**: It receives a path to an XML file. It parses the file into an XML tree, adds two-space indentation, and writes the formatted XML back to the same file. If anything goes wrong, it quietly leaves the file as it was.

**Call relations**: `unpack_docx` calls this for every extracted `.xml` and `.rels` file right after unzipping. It is an early cleanup step before the script performs deeper edits on the main document.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This function replaces curly quote characters with explicit XML character references. That makes those characters stable and visible in the unpacked text files.

**Data flow**: It receives a file path, reads the file as text, looks for curly single or double quotation marks, and writes the file back with those characters replaced by forms like `&#x201C;`. If there are no curly quotes, it does nothing. If reading or writing fails, it silently leaves the file unchanged.

**Call relations**: `unpack_docx` calls this near the end for every XML-like file. It runs after formatting and document cleanup, so the final unpacked folder has consistent quote representation.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This function simplifies `document.xml` by combining neighboring Word text runs that have the same formatting. This reduces clutter caused by Word splitting text into many tiny pieces.

**Data flow**: It receives the path to `document.xml`. If the file is missing, it returns zero. Otherwise it parses the XML, removes spelling or grammar proofing markers, removes run attributes related to Word revision IDs, finds parent elements that contain runs, and asks `_merge_runs_in` to combine matching neighbors inside each parent. If any runs were merged, it writes the XML back and returns the number of absorbed runs.

**Call relations**: `unpack_docx` calls this only for the main document XML and only when run merging is enabled. It delegates the per-container merging work to `_merge_runs_in`, which in turn uses smaller helpers to compare formatting and join text.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This function creates a standard text fingerprint for a run’s formatting. It lets the script tell whether two Word runs have the same style details.

**Data flow**: It receives one run element from the XML. It looks for that run’s formatting child, called `w:rPr`; if none exists, it returns `None`. If formatting exists, it converts that formatting XML into a canonical, normalized string and returns it.

**Call relations**: `_merge_runs_in` calls this while scanning neighboring runs. The returned fingerprint is what lets that function decide whether two adjacent runs are safe to combine.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This function combines matching neighboring runs inside one parent XML element. It is the detailed worker behind the larger run-merging cleanup.

**Data flow**: It receives a container element, such as a paragraph-like XML node. It walks through the container’s direct children, groups consecutive run elements whose formatting fingerprint matches, moves the non-formatting contents from later runs into the first run of each group, removes the now-empty donor runs, joins neighboring text pieces inside the anchor run, and returns how many runs were absorbed.

**Call relations**: `_merge_adjacent_runs` calls this once for each parent element that contains runs. It uses `_canonical_rpr` to compare formatting and `_join_adjacent_text` to tidy up text after several runs have been folded into one.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This function joins neighboring text nodes inside a single run. After runs are merged, it prevents the resulting run from still being split into needless text fragments.

**Data flow**: It receives one run element. It scans its child nodes, and whenever two neighboring children are both text nodes, it combines their text into the first one and removes the second. If the merged text starts or ends with a space, it marks the XML so that space should be preserved.

**Call relations**: `_merge_runs_in` calls this after it has moved text from donor runs into an anchor run. It is the final polishing step inside each merged run.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word tracked changes in `document.xml`. It combines neighboring insertions or deletions from the same author so the document history is less fragmented.

**Data flow**: It receives the path to `document.xml`. If the file is missing, it returns zero. Otherwise it reads the XML while preserving existing blank text, finds paragraph and table-cell containers, and for each container asks `_coalesce_in` to combine adjacent insertions and deletions. If anything was combined, it writes the XML back and returns the number of absorbed change elements.

**Call relations**: `unpack_docx` calls this before run merging when tracked-change coalescing is enabled. It delegates each container and change type to `_coalesce_in`, which then performs the smaller grouping and merging work.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This function looks inside one container for tracked changes of one kind, either insertions or deletions, and groups them by author. It prepares those groups so adjacent changes from the same person can be merged.

**Data flow**: It receives an XML container and a change type string such as `ins` or `del`. It finds direct child elements of that type, groups matching elements by their author attribute, sends each group to `_merge_change_run`, and returns the total number of change elements that were absorbed.

**Call relations**: `_coalesce_tracked_changes` calls this for both insertion and deletion changes in each paragraph or table cell. It hands the actual merge decision to `_merge_change_run`.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This function merges a sequence of tracked-change elements when they are truly next to each other. It keeps the first change as the anchor and folds later adjacent changes into it.

**Data flow**: It receives a list of insertion or deletion elements from the same author group. It starts with the first as the anchor, checks each later element with `_changes_adjacent`, moves the later element’s children into the anchor when safe, preserves any trailing text by attaching it nearby, removes the later element from the XML tree, and returns how many elements were absorbed. If a later change is not adjacent, it becomes the new anchor.

**Call relations**: `_coalesce_in` calls this after grouping changes. It depends on `_changes_adjacent` to avoid merging changes that only look related but are separated by meaningful XML content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This function answers a safety question: are two tracked-change elements next to each other, with only whitespace or comments between them? It prevents the script from merging changes across real document content.

**Data flow**: It receives two XML elements. It finds their shared parent, checks their positions among the parent’s children, looks at any text or nodes between them, and returns `true` only when there is no meaningful content in the gap. If the parent is missing or either element cannot be found, it returns `false`.

**Call relations**: `_merge_change_run` calls this before folding one tracked-change element into another. Its answer controls whether a merge is safe or whether the later change must remain separate.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `manual document-editing script run`

A DOCX file is really a zip package full of XML files. Word comments are not stored in just one place: Word expects a main comments file, newer “threading” and identity files, relationship entries, and content-type entries. This file does that bookkeeping so another tool or person does not have to remember every required piece.

The main flow takes an unpacked DOCX directory, a comment id, text, author details, and optionally a parent comment id for a reply. If this is the first comment, it copies starter XML templates into the word folder and registers those files with the package. Then it creates four matching XML entries: the visible comment text, extra information used for threaded comments, a durable identifier, and a timestamped extensible record. Think of these as four labels on the same parcel, each needed by a different part of Word.

One important limit: this script does not place the comment markers around text in document.xml. After it writes the comment files, it prints instructions showing what marker XML must be inserted separately. Also, the comment text is expected to already be XML-safe, so characters like ampersands must already be escaped.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character hexadecimal label used by Word to identify a comment paragraph or durable comment id. It gives each inserted comment a fresh-looking internal tag.

**Data flow**: It takes no input. It asks the random number generator for a number in Word’s expected range, formats that number as uppercase hexadecimal text, and returns the text.

**Call relations**: When insert_comment starts building a new comment, it calls this twice: once for the paragraph id and once for the durable id. Those ids are then passed into the XML-building helpers so all the comment-related files point to the same new comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces curly quote characters with XML character references. This keeps those typographic quotes represented in a safe, explicit way when XML is written back to disk.

**Data flow**: It receives a text string. It scans for left and right curly single and double quotes, replaces each with its XML reference form, and returns the changed string.

**Call relations**: _serialize_xml calls this just before bytes are written out. It is the final cleanup step after XML has been converted to text.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an in-memory XML tree that the script can search or edit. This is the common doorway from files into structured XML objects.

**Data flow**: It receives a file path. It reads the file’s bytes, parses those bytes as XML, and returns the root XML element.

**Call relations**: _append_element_to_file uses it before adding new XML, _ensure_registrations uses it before editing package registration files, and _resolve_parent_paragraph uses it to search existing comments.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an in-memory XML tree back into saved XML bytes. It also applies the script’s curly-quote escaping rule before the bytes are written.

**Data flow**: It receives the root XML element. It converts the tree into UTF-8 XML with an XML declaration, replaces curly quotes with safe XML references, and returns bytes ready for writing.

**Call relations**: _append_element_to_file calls this after it has attached a new XML child. This function is the bridge from edited XML objects back to file contents.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main Word comment XML entry: the author, date, id, and visible comment text. This is the part that contains what a user would recognize as the comment itself.

**Data flow**: It receives the comment id, author name, author initials, timestamp, paragraph id, and body text. It creates a Word XML comment element with a paragraph, a reference marker, formatting, and the text, then returns that element.

**Call relations**: insert_comment calls this after creating ids and a timestamp. The returned element is handed to _append_element_to_file so it can be added to comments.xml.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra Word XML entry that supports comment status and threaded replies. If the new comment is a reply, this element records which parent paragraph it belongs under.

**Data flow**: It receives the new comment’s paragraph id and, optionally, the parent comment’s paragraph id. It creates a commentsExtended XML element marked as not done, adds the parent link when present, and returns it.

**Call relations**: insert_comment calls this after it has resolved any parent reply information. The result is appended to commentsExtended.xml so Word can understand comment threading details.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML entry that connects a comment paragraph id to a durable id. A durable id is a stable internal identifier Word can use beyond the visible comment number.

**Data flow**: It receives the paragraph id and durable id. It creates a commentsIds XML element containing both values and returns it.

**Call relations**: insert_comment calls this for every new comment. The returned element is written into commentsIds.xml alongside the other records for the same comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the newer Word XML entry that stores the durable id with a UTC timestamp. This supports Word’s more recent comment metadata format.

**Data flow**: It receives the durable id and timestamp. It creates a commentsExtensible XML element with those two attributes and returns it.

**Call relations**: insert_comment calls this after generating the durable id and current time. The element is appended to commentsExtensible.xml to complete the set of metadata Word expects.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the internal paragraph id for an existing parent comment. This is needed because a reply links to the parent’s paragraph id, not just the parent’s visible comment id.

**Data flow**: It receives the path to comments.xml and the parent comment id. It parses the XML, searches for a comment with that id, looks inside it for a paragraph id, and returns that id if found; otherwise it returns nothing.

**Call relations**: insert_comment calls this only when the caller is adding a threaded reply. Its answer is passed into _build_extended_element so the reply can be tied to the parent comment.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to the end of an existing XML file and saves the file again. It is the script’s standard “open, add, write back” operation.

**Data flow**: It receives a file path and an XML child element. It parses the file into a tree, appends the child to the root element, serializes the updated tree, and writes the bytes back to the same file.

**Call relations**: insert_comment uses this repeatedly, once for each comment-related XML file. It relies on _parse_xml_file to read the current file and _serialize_xml to prepare the updated version for disk.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure the DOCX package knows about the comment files. Without these relationship and content-type entries, Word may ignore the new XML files even if they exist.

**Data flow**: It receives the unpacked DOCX base directory. It checks document.xml.rels for links to the comment files and [Content_Types].xml for file type declarations; if the comment entries are missing, it adds them and writes the files back.

**Call relations**: insert_comment calls this only when it is creating the comment XML files for the first time. This prepares the package-level wiring before the actual comment records are appended.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds a comment, or a reply to an existing comment, into the comment-related XML files of an unpacked DOCX folder. This is the main function other code or the command-line script uses.

**Data flow**: It receives the unpacked directory path and a CommentSpec containing the comment id, text, author, initials, and optional parent id. It checks that the word folder exists, creates ids and a timestamp, copies templates if this is the first comment, registers the files, builds each required XML element, appends those elements to their files, and returns the new paragraph id plus a success or error message. If the parent reply id cannot be found, it returns an error; notably, the main comments.xml entry has already been appended before that parent check finishes.

**Call relations**: The command-line part of this file builds a CommentSpec from user arguments and calls insert_comment. Inside, insert_comment coordinates the smaller helpers: it asks _make_hex_tag for ids, uses the build functions to create XML pieces, uses _resolve_parent_paragraph for replies, calls _ensure_registrations for first-time setup, and hands finished XML pieces to _append_element_to_file.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`entrypoint` · `on-demand document packaging`

A DOCX file is really a ZIP archive full of XML files and related resources. This script is the “put it back in the box” step after someone has unpacked and edited that folder. Without it, the edited document parts would remain as loose files and could not be opened as a normal Word document.

The main flow is simple. First, it checks that the input is a real directory and that the output path ends in `.docx`. Then it copies the whole directory into a temporary staging area, like making a safe workbench copy before packing a suitcase. In that staging copy, it visits every XML file and relationship file (`.rels`) and removes indentation-only whitespace where it is safe to do so. It deliberately avoids Word text elements, because spaces inside document text can be meaningful.

After cleanup, it creates the output folder if needed and writes every staged file into a compressed ZIP archive with the `.docx` extension. If run directly from the command line, it accepts an input directory and output file path, prints the result message, and exits with an error code if packing failed.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing function. It takes a folder that represents an unpacked DOCX file, cleans its XML parts, and writes a new `.docx` archive.

**Data flow**: It receives an input directory path and an output file path. It first checks that the input is a directory and the output name ends in `.docx`; if either check fails, it returns no output path and an error message. If the checks pass, it copies the input into a temporary staging folder, asks `_strip_xml_whitespace` to clean each XML and `.rels` file, then writes all staged files into a compressed DOCX archive. It returns the path to the created file and a success message.

**Call relations**: When the script is used from the command line, the parsed arguments are passed into `pack_docx`. During its work, `pack_docx` relies on standard library tools to create a temporary workspace, copy the folder, and build the ZIP archive, and it calls `_strip_xml_whitespace` for the XML cleanup step before the final archive is written.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML-style file by removing whitespace that is only formatting, while preserving spaces that may be part of visible Word document text.

**Data flow**: It receives the path to one XML or `.rels` file. It parses the file into an XML tree, walks through each element, skips text-bearing Word elements where whitespace may matter, removes blank-only text and tail whitespace elsewhere, and removes unusual callable-tag children that should not be written back. It then writes the cleaned XML back to the same file using UTF-8 encoding. If parsing or writing fails, it prints an error message to standard error and raises the failure again.

**Call relations**: `pack_docx` calls this helper once for each XML and relationship file in the temporary staging copy. Its output is not a separate return value; instead, it changes the staged file in place so that the later ZIP-writing step includes the cleaned version.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).
