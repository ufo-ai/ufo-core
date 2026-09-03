# Office DOCX document utilities  `stage-17.2`

This stage provides command-line tools for working with Microsoft Word DOCX files without opening Word. A DOCX file is really a zipped bundle of XML files, where XML is structured text that stores the document’s content and settings. These helpers support behind-the-scenes document editing and cleanup.

The unpack tool opens a .docx file into a normal folder, exposing its XML parts so other tools or people can inspect and edit them. It also cleans the main document XML to remove distracting formatting noise. The comment tool works on that unpacked folder and adds the extra XML records Word needs for a proper comment, then tells the user where to place the matching comment markers in the document text. The pack tool reverses the unpack step: it tidies XML spacing and zips the folder back into a usable .docx file. Finally, the accept changes tool makes a clean version of a document by accepting all tracked edits through LibreOffice in the background. Together, these scripts form a small workshop for taking DOCX files apart, adjusting them, and putting them back safely.

## Files in this stage

### Tracked-change cleanup
Accept tracked changes in a DOCX through LibreOffice to produce a clean copy without manual editing.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `document cleanup`

This file solves a practical document-cleanup problem: a DOCX file may contain visible edits, deletions, and comments from “track changes,” but many later steps need the final accepted version. The script takes an input DOCX, copies it to the requested output path, then asks LibreOffice to accept every tracked change in that copy.

LibreOffice is run in “headless” mode, meaning it works without opening a visible window. To make LibreOffice perform the exact action, the script installs a tiny LibreOffice Basic macro into a temporary LibreOffice user profile. A macro is like a recorded instruction sheet inside LibreOffice. Here, the instruction says: accept all tracked changes, save the document, and close it.

One important detail is that LibreOffice can sometimes keep running even after it has already saved the finished document. Because of that, if the LibreOffice command times out, this script treats the timeout as success. That may look odd, but it matches the observed behavior: the work is often done before LibreOffice gets stuck. Without this file, another part of the system would need to automate LibreOffice manually or risk passing around DOCX files that still contain unresolved tracked edits.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This prepares the environment settings used when starting LibreOffice. In particular, it tells LibreOffice to use a non-visual backend so it can run safely without a desktop window.

**Data flow**: It starts with the current process environment, copies all existing settings, adds the LibreOffice display setting, and returns the updated environment dictionary. It does not change the real process environment directly.

**Call relations**: When the script needs to start LibreOffice, both the macro setup step and the final document-processing step ask this helper for the right environment. That keeps the LibreOffice launch settings consistent in both places.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This builds the command-line argument that tells LibreOffice to use this script’s temporary user profile. A LibreOffice profile is a private folder for settings and installed macros, like a separate workspace.

**Data flow**: It reads the fixed profile directory path chosen by the script and turns it into the exact command-line text LibreOffice expects. The result is a string passed into LibreOffice commands.

**Call relations**: The macro installation step uses this profile argument so the macro lands in the right private LibreOffice profile. The main document-processing step uses the same argument so LibreOffice can find and run that macro.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure the LibreOffice macro needed to accept tracked changes is installed before any document is processed. If the macro is already present, it leaves it alone; otherwise, it creates the needed folder and writes the macro file.

**Data flow**: It checks the expected macro file on disk. If the file already contains the required macro, it reports success. If the macro folder does not exist, it briefly starts LibreOffice to initialize the profile, creates the macro directory, writes the macro XML file, and returns success.

**Call relations**: The main accept_tracked_changes function calls this before launching LibreOffice on the DOCX file. Inside, it relies on _profile_arg to point LibreOffice at the temporary profile and _soffice_env to start LibreOffice in headless-friendly mode.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main worker for the script. It checks that the input is a DOCX file, copies it to the output location, installs the LibreOffice macro if needed, then runs LibreOffice to accept all tracked changes in the copied file.

**Data flow**: It receives two file paths: the source DOCX and the desired output DOCX. It first validates that the source exists and has the .docx extension. Then it creates the output folder if needed, copies the source file to the destination, ensures the macro is available, and starts LibreOffice on the copied file. It returns a pair whose first value is always None and whose second value is a human-readable success or error message.

**Call relations**: This function is called by the command-line block when someone runs the script directly. It coordinates the helper functions: _ensure_macro prepares LibreOffice, _profile_arg tells LibreOffice which profile to use, and _soffice_env supplies the safe background-running environment. It also calls external file-copying and LibreOffice commands to do the actual work.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### DOCX package editing
Unpack a DOCX into editable XML, add Word-compatible comments, and repack the folder into a finished document.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual DOCX unpacking and XML cleanup`

A .docx file is really a ZIP package full of XML files. This script opens that package, extracts it into a folder, and makes the XML easier for humans and tools to read. Without it, anyone trying to inspect or edit a Word document at the XML level would have to manually unzip it and deal with cramped, repetitive Word markup.

The main flow is: check that the input exists and is a .docx file, unzip it, pretty-print every XML and relationship file, then focus on word/document.xml, which contains the document body. Two cleanup steps can run there. First, tracked changes can be coalesced, meaning nearby insertions or deletions from the same author are joined into one larger change. Second, adjacent Word “runs” can be merged. A run is a small stretch of text with the same formatting; Word often splits text into many tiny runs, which makes XML hard to read and diff. The script joins neighboring runs when their formatting matches.

Finally, it replaces literal curly quote characters with XML numeric entities, so those characters stay explicit in the saved XML. Most helper steps are best-effort: if an individual XML file cannot be indented or quote-replaced, the script quietly leaves it alone.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker for the script. It takes a .docx file, expands it into a folder, tidies the XML, optionally simplifies tracked changes and text runs, and returns both counts and a human-readable summary.

**Data flow**: It receives an input file path, an output folder path, and two yes-or-no options. It first checks that the file exists and has a .docx extension. Then it creates the output folder, extracts the ZIP contents there, finds XML-style files, indents them, optionally cleans word/document.xml, replaces curly quotes in the extracted files, and returns an UnpackResult plus a message. If the file is missing, not a .docx, or not a valid ZIP archive, it returns no result and an error message.

**Call relations**: This is the top-level routine used by the command-line block at the bottom of the file, and it could also be called from other Python code. During its run it hands work to _indent_xml for formatting, _coalesce_tracked_changes for tracked-change cleanup, _merge_adjacent_runs for Word run cleanup, and _replace_curly_quotes for final text escaping.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This helper makes one XML file easier to read by adding normal line breaks and indentation. It is like reformatting a dense paragraph into an outline.

**Data flow**: It receives the path to one XML file. It parses the file as XML, asks lxml to indent the tree with two spaces, and writes the formatted XML back to the same file. If anything goes wrong, it quietly does nothing, so one problematic file does not stop the whole unpacking process.

**Call relations**: unpack_docx calls this once for each extracted .xml and .rels file right after unzipping. It does not call any project-specific helpers; it relies on lxml to parse, indent, and serialize the XML.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This helper rewrites curly quotation marks as explicit XML character references. That keeps those special characters visible and stable in the saved XML text.

**Data flow**: It receives the path to one extracted XML-like file. It reads the file as UTF-8 text, checks whether any curly single or double quotes are present, and if so replaces each one with its numeric XML entity such as &#x201C;. It writes the changed text back to the same file; if reading or writing fails, it leaves the file alone.

**Call relations**: unpack_docx calls this near the end for every XML and relationship file it found. It is a final cleanup pass after indentation, tracked-change coalescing, and run merging have already had their chance to rewrite XML.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word’s main document XML by joining neighboring text runs that have the same formatting. It reduces clutter created by Word splitting text into many tiny pieces.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns zero. Otherwise it parses the XML, removes Word proofing-error markers, strips run attributes whose names contain rsid, finds every parent element that contains runs, and asks _merge_runs_in to merge compatible neighbors inside each parent. If any runs were absorbed, it writes the cleaned XML back and returns the number of removed run elements.

**Call relations**: unpack_docx calls this only when document.xml exists and the merge-runs option is enabled. It delegates the detailed per-container merging to _merge_runs_in, then saves the full document tree if that helper reports changes.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This helper creates a comparable fingerprint for a run’s formatting. It lets the script decide whether two Word runs have the same run properties and can safely be joined.

**Data flow**: It receives one run XML element. It looks for that run’s formatting child, called w:rPr in WordprocessingML. If no formatting child exists, it returns None. If one exists, it serializes that formatting element in a canonical, normalized XML form and returns the resulting string.

**Call relations**: _merge_runs_in calls this while scanning neighboring runs. The returned formatting fingerprint is the key used to decide whether the current run belongs with the active merge group or starts a new group.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This function performs the actual merging of adjacent run elements inside one parent element. It keeps the first run and moves later runs’ content into it when their formatting matches.

**Data flow**: It receives a container XML element, such as a paragraph-like parent. It walks through that container’s direct children, grouping only consecutive run elements with identical formatting fingerprints. For each group of two or more, it keeps the first run, moves all non-formatting children from the later runs into that first run, removes the now-empty donor runs, joins neighboring text nodes inside the anchor, and returns how many donor runs were removed.

**Call relations**: _merge_adjacent_runs calls this for each parent element that contains Word runs. This function uses _canonical_rpr to compare formatting and _join_adjacent_text to clean up the anchor run after child nodes have been moved.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This helper tidies a single run after other runs have been merged into it. It combines neighboring text nodes so the run does not contain unnecessary separate text fragments.

**Data flow**: It receives one run XML element. It looks through the run’s children in order. Whenever two neighboring children are both Word text nodes, it concatenates their text into the first node, preserves leading or trailing spaces when needed using the XML space attribute, removes the second node, and continues until no adjacent text-node pair remains. It returns nothing; the run element is changed in place.

**Call relations**: _merge_runs_in calls this after moving donor content into an anchor run. It is the final polish step that turns a technically merged run into a cleaner, simpler run.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word tracked-change markup by joining consecutive insertions or deletions from the same author. It makes the edit history less fragmented while preserving the actual inserted or deleted content.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns zero. Otherwise it reads and parses the XML while keeping existing blank text, finds paragraph and table-cell containers, and for each container asks _coalesce_in to process insertions and deletions. If any tracked-change elements were merged, it writes the XML back and returns the number of absorbed change elements.

**Call relations**: unpack_docx calls this only when document.xml exists and the coalesce-changes option is enabled. It coordinates the tracked-change cleanup and delegates the detailed work for each container and change kind to _coalesce_in.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This helper looks inside one container for tracked changes of one type, either insertions or deletions, and groups nearby changes by author. It prepares the right sets of elements for merging.

**Data flow**: It receives a container XML element and a change type string such as ins or del. It collects direct child elements of that type. If fewer than two exist, it returns zero. Otherwise it groups consecutive matching change elements that have the same author attribute, asks _merge_change_run to merge each group where possible, and returns the total number of absorbed elements.

**Call relations**: _coalesce_tracked_changes calls this for each paragraph and table cell, once for insertions and once for deletions. This function then hands each same-author run of change elements to _merge_change_run, which checks actual XML adjacency before modifying anything.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This function merges a sequence of tracked-change elements when they are truly next to each other in the XML. It keeps the first change element and moves later change content into it.

**Data flow**: It receives a list of insertion or deletion elements that already share the same change type and author grouping. Starting with the first as the anchor, it checks each later element with _changes_adjacent. If the later element is adjacent, its children are moved into the anchor, its tail text is preserved on a nearby node or parent, and the later element is removed. If it is not adjacent, that later element becomes the new anchor. The function returns how many later elements were absorbed.

**Call relations**: _coalesce_in calls this after grouping tracked changes by author. This function depends on _changes_adjacent to avoid merging changes that only look related but are separated by real document content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This helper answers a careful yes-or-no question: are two tracked-change elements next to each other, ignoring only comments and whitespace? It protects the script from merging changes across meaningful content.

**Data flow**: It receives two XML elements. It gets their shared parent, finds each element’s position among that parent’s children, and examines whatever sits between them. If it sees any real element other than an XML comment, or any non-whitespace text between them, it returns false. If only whitespace and comments separate them, it returns true. It also returns false if the parent or positions cannot be found.

**Call relations**: _merge_change_run calls this before absorbing a later tracked-change element into the current anchor. Its result decides whether merging is safe or whether the later change must remain separate.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`io_transport` · `manual DOCX editing / command-line run`

A DOCX file is really a zipped folder full of XML files. Adding a comment is not as simple as writing one line of text: Word stores comment text, thread information, stable IDs, timestamps, relationships, and content type registrations in different places. This file is the small tool that keeps those pieces in sync. Think of it like adding a library book: the book itself goes on a shelf, but the catalog, barcode record, and checkout system also need matching entries.

The main path is `insert_comment`. It checks that the unpacked DOCX has a `word` folder. If this is the first comment, it copies template comment files into place and registers them in the DOCX package files so Word knows they exist. It then creates a new comment with an author, initials, time, unique paragraph ID, and comment text. For replies, it looks up the parent comment’s paragraph ID so Word can display the thread correctly.

The script updates four files: `comments.xml` for the visible comment body, `commentsExtended.xml` for thread state, `commentsIds.xml` for durable identifiers, and `commentsExtensible.xml` for newer Word metadata. It does not edit `document.xml` directly. Instead, after it writes the comment data, it prints the XML markers the user must insert around the text being annotated.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`io_transport` · `document packaging or command-line invocation`

A DOCX file is really a ZIP archive full of XML files and related assets. This script is the “put it back in the box” step after someone has unpacked and edited that folder. Without it, the edited directory would not become a file that Microsoft Word or similar tools can open as a document.

The main function, `pack_docx`, first checks two simple safety rules: the input must be a directory, and the output name must end in `.docx`. It then copies the whole input folder into a temporary staging area. This is like making a workbench copy before cleaning and boxing something up, so the original files are not changed.

Before creating the final ZIP archive, it walks through XML files and relationship files ending in `.rels`. For each one, `_strip_xml_whitespace` removes whitespace-only text that exists just for formatting the XML file itself. It carefully avoids Word text elements, because spaces inside document text can be meaningful. Finally, it writes every staged file into a compressed ZIP archive with the requested `.docx` name.

The script can also be run directly from the command line. In that mode, it prints a success or error message and exits with a failure code if the packing request was invalid.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This function builds a `.docx` file from a folder that contains the unpacked parts of a Word document. It is the main safe wrapper around copying, cleaning XML, and creating the final ZIP-based document file.

**Data flow**: It receives an input folder path and an output file path. It checks that the input is a real directory and that the output ends in `.docx`; if either check fails, it returns no file path and an error message. Otherwise, it copies the folder into a temporary staging area, asks `_strip_xml_whitespace` to clean each XML and `.rels` file there, creates the output folder if needed, writes all staged files into a compressed DOCX archive, and returns the output path plus a success message.

**Call relations**: This is the top-level packing routine used by the command-line part of the script or by any Python caller that wants the same behavior. During its work it calls `_strip_xml_whitespace` for each XML-like file before handing the staged files to `zipfile.ZipFile` to make the final archive.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that only exists to make the XML source look pretty. It deliberately preserves text-related Word elements, because changing their spaces could change what a reader sees in the document.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through each element, skips elements that contain document text or instructions, removes blank-only text and tail whitespace elsewhere, removes unusual callable-tag children, then writes the cleaned XML back to the same file as UTF-8 bytes. If parsing or writing fails, it prints an error to standard error and raises the exception so the caller knows packing failed.

**Call relations**: This function is called by `pack_docx` while the document is still in the temporary staging folder. It does the focused XML cleanup step, then gives control back to `pack_docx`, which continues by adding the cleaned file to the final DOCX archive.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).
