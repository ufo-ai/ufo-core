# Office DOCX Command-Line Utilities  `stage-10.3.9`

This stage is a set of command-line tools for working on Microsoft Word DOCX files behind the scenes. It is not the main user interface. It supports document automation by turning a Word file into parts that can be edited safely, changing those parts, and building the Word file again.

The process often starts with accept_changes.py, which makes a clean copy of a document by accepting all tracked edits. It uses LibreOffice “headless,” meaning LibreOffice runs invisibly in the background. Next, unpack.py opens the DOCX package like a zip file and expands it into a folder of XML files. XML is structured text that Word uses internally. This script also simplifies messy Word markup so later steps are easier.

comment.py works on that unpacked folder. It adds the hidden records Word needs for a new comment or a reply, then reports the small marker that still must be placed in the document text. Finally, pack.py gathers the folder back into a DOCX file and removes extra whitespace so the package stays neat.

## Files in this stage

### DOCX XML Workflow
Utilities prepare Word documents, unpack them into editable XML, add comment metadata, and repack the cleaned package into a DOCX.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `on-demand document processing or CLI run`

This file solves a practical document-cleanup problem: a DOCX file may contain tracked edits, and the system needs an output file where those edits are accepted as final text. Python libraries do not reliably support every detail of Word change tracking, so this script asks LibreOffice to do the job instead.

The script first copies the input document to the requested output path, so the original file is not changed. Then it makes sure LibreOffice has a small Basic macro installed. A macro is a short script that LibreOffice can run inside the document, like a recorded office action. This macro tells LibreOffice to run its built-in “Accept All Tracked Changes” command, save the document, and close it.

LibreOffice is launched with a temporary user profile under `/tmp`, so this automation does not depend on or alter a real user’s LibreOffice settings. It also sets an environment option that helps LibreOffice run without a display.

One important quirk is handled deliberately: LibreOffice may hang after the macro has already saved the file. Because of that, a timeout is treated as success. In this case, the script assumes the document work is done even if the LibreOffice process did not exit cleanly.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This builds the environment settings used when starting LibreOffice. It adds one setting that encourages LibreOffice to run without needing a visible desktop window.

**Data flow**: It starts with the current process environment, which is the set of variables already available to the script. It copies those values, adds `SAL_USE_VCLPLUGIN=svp`, and returns the modified environment dictionary for LibreOffice subprocesses to use.

**Call relations**: Whenever the script starts LibreOffice, either to create the temporary profile in `_ensure_macro` or to process the document in `accept_tracked_changes`, it asks `_soffice_env` for the right environment first.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This creates the command-line argument that tells LibreOffice to use this script’s temporary profile directory. A profile is LibreOffice’s private settings area, like a separate workspace.

**Data flow**: It reads the fixed `PROFILE_DIR` path and formats it into the special `-env:UserInstallation=...` argument that LibreOffice understands. The returned string is later placed into LibreOffice command lines.

**Call relations**: Both `_ensure_macro` and `accept_tracked_changes` call this before launching LibreOffice, so the macro installation and the document processing happen in the same isolated LibreOffice profile.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure LibreOffice has the small macro needed to accept tracked changes. Without this macro, the later LibreOffice run would not know what office command to execute inside the DOCX file.

**Data flow**: It first checks whether the macro file already exists and contains the expected `AcceptAllTrackedChanges` macro. If not, it may start LibreOffice briefly to initialize the temporary profile, creates the macro folder if needed, writes the macro XML file, and returns `True` to say the macro is ready.

**Call relations**: `accept_tracked_changes` calls this after copying the input file and before launching LibreOffice on the output file. Inside, `_ensure_macro` uses `_profile_arg` and `_soffice_env` to start LibreOffice safely in the temporary profile when profile setup is needed.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main work function: it takes an input DOCX file, creates an output DOCX file, and accepts all tracked changes in that output copy. It returns a message saying either what succeeded or what went wrong.

**Data flow**: It receives two file paths: the source document and the desired output document. It checks that the source exists and has a `.docx` extension, creates the output folder if needed, and copies the source to the output path. Then it ensures the LibreOffice macro is installed, starts LibreOffice in headless mode to run that macro on the copied document, and returns a success or error message. If LibreOffice times out, it still returns success because the file is expected to have already been saved.

**Call relations**: This is the function used by the command-line block at the bottom of the file. It coordinates the helper functions: `_ensure_macro` prepares the macro, `_profile_arg` points LibreOffice at the right temporary profile, and `_soffice_env` supplies the safe background-running environment before `subprocess.run` starts LibreOffice.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual DOCX unpacking from command line or helper call`

A .docx file is really a ZIP archive full of XML files. This script opens that archive, extracts its contents into a folder, and then cleans up the XML so it is easier to inspect, edit, or compare in source control. Without this step, a Word document’s XML can be hard to read because Word often splits one visible sentence into many tiny “runs” of text, records tracked changes in many small neighboring pieces, and stores curly quote characters directly instead of as XML-friendly entities.

The main flow is simple: check that the input exists and is a .docx file, unzip it, pretty-print the XML with indentation, optionally combine nearby tracked changes from the same author, optionally merge adjacent text runs that have the same formatting, and finally replace curly quotes with explicit XML character references. Think of it like unpacking a suitcase and then folding the clothes neatly so another person can see what is inside.

The file is careful to focus its deeper cleanup on word/document.xml, the main body of the Word document. It leaves most failures in formatting helpers quiet, so a single malformed side file does not stop the whole unpack operation. The result is both a small UnpackResult summary for code and a human-readable message for command-line use.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main workhorse. It takes a .docx file, extracts it into a folder, cleans the XML, and reports what it changed.

**Data flow**: It receives an input file path, an output folder path, and two yes-or-no options for merging runs and coalescing tracked changes. It checks the file, creates the destination folder, opens the .docx as a ZIP archive, extracts all files, finds XML-related files, formats them, cleans the main document XML if present, replaces curly quotes, and returns either an UnpackResult plus a summary message or no result plus an error message.

**Call relations**: When this script is used, this function coordinates the whole pipeline. It calls _indent_xml for readability, _coalesce_tracked_changes to combine nearby tracked edits, _merge_adjacent_runs to simplify Word text fragments, and _replace_curly_quotes as a final text cleanup step.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This makes an XML file easier for humans to read by adding line breaks and indentation. It is cosmetic, but very useful when someone needs to inspect or edit the extracted document files.

**Data flow**: It receives the path to one XML-like file. It reads the file as XML, asks lxml to indent the tree, and writes the pretty-printed XML back to the same file. If parsing or writing fails, it quietly leaves the file unchanged.

**Call relations**: unpack_docx calls this once for each extracted .xml and .rels file soon after unzipping. It prepares the files before the more Word-specific cleanup steps happen.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This replaces curly quote characters with explicit XML character references. That keeps those characters visible and stable in the XML text.

**Data flow**: It receives a file path, reads the file as UTF-8 text, searches for curly single or double quotes, and writes the file back with those characters replaced by numeric XML entities. If there are no curly quotes, it does nothing; if reading or writing fails, it quietly leaves the file alone.

**Call relations**: unpack_docx calls this near the end for each extracted XML-related file. It is the final cleanup pass after indentation, tracked-change coalescing, and run merging.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This simplifies Word’s main document XML by merging neighboring text runs that have the same formatting. A “run” is a small stretch of text with formatting; Word often creates too many of them, which makes the XML noisy.

**Data flow**: It receives the path to word/document.xml. If the file exists, it parses the XML, removes proofing-error markers, strips run identifiers that would prevent clean comparison, finds parent containers that hold text runs, asks _merge_runs_in to merge compatible neighbors, and writes the document back only if something was actually merged. It returns the number of runs absorbed into earlier runs.

**Call relations**: unpack_docx calls this when run merging is enabled. It delegates the detailed per-container merging to _merge_runs_in, which in turn compares formatting and joins text nodes.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This creates a stable fingerprint for a run’s formatting. It lets the merge logic decide whether two neighboring text runs are formatted the same way.

**Data flow**: It receives one run element from the XML. It looks for that run’s formatting child element, called rPr in WordprocessingML, and converts it into a canonical string form. If the run has no formatting element, it returns None.

**Call relations**: _merge_runs_in calls this while scanning runs in a container. The returned fingerprint is the basis for deciding whether consecutive runs belong together.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This does the actual merging of adjacent compatible text runs inside one parent XML element. It reduces many small Word-generated pieces into fewer, clearer pieces.

**Data flow**: It receives a container element, such as a paragraph-like parent. It walks through its children, groups consecutive run elements that have the same formatting fingerprint, moves the non-formatting contents of later runs into the first run of each group, removes the now-empty later runs, joins neighboring text nodes inside the merged run, and returns how many runs were absorbed.

**Call relations**: _merge_adjacent_runs calls this for each container that contains runs. It calls _canonical_rpr to compare formatting and _join_adjacent_text to clean up text nodes after elements have been moved.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This tidies a merged run by combining neighboring text nodes into one text node. It also preserves leading or trailing spaces when XML would otherwise risk losing them.

**Data flow**: It receives a run element. It scans the run’s child nodes, and whenever two text nodes sit next to each other, it joins their text into the first node, removes the second node, and sets or clears the XML space-preservation marker based on whether the merged text starts or ends with a space. It changes the run in place and returns nothing.

**Call relations**: _merge_runs_in calls this after it has moved content from several runs into one anchor run. This function finishes the cleanup so the merged run is not still internally fragmented.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This combines neighboring tracked insertions or deletions from the same author in the main Word document XML. It makes Word’s change history easier to read and less chopped up.

**Data flow**: It receives the path to word/document.xml. If the file exists, it reads and parses the XML while keeping whitespace, finds paragraph and table-cell containers, and for each one asks _coalesce_in to combine adjacent insertion and deletion elements. If anything was combined, it writes the XML back and returns the number of change elements removed.

**Call relations**: unpack_docx calls this when tracked-change coalescing is enabled. It sets up the document-wide search and hands each relevant container to _coalesce_in for the smaller, local merge work.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This looks inside one XML container for tracked changes of one kind, either insertions or deletions, and groups nearby changes by author. It prepares the right batches for merging.

**Data flow**: It receives a container element and a change type string such as "ins" or "del". It finds direct child change elements of that type, groups them by their author attribute, sends each author group to _merge_change_run, and returns the total number of change elements absorbed.

**Call relations**: _coalesce_tracked_changes calls this for each paragraph or table cell and for both insertion and deletion changes. It hands off each same-author sequence to _merge_change_run, which checks whether the elements are truly adjacent before merging them.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This merges a sequence of tracked-change elements when they are next to each other. It keeps the first change element and moves later change contents into it.

**Data flow**: It receives a list of tracked-change XML elements that share the same author grouping. Starting with the first element as the anchor, it checks each later element with _changes_adjacent; when two are adjacent, it moves the later element’s children into the anchor, preserves any trailing text, removes the later element from its parent, and counts it as absorbed. If a later element is not adjacent, it becomes the new anchor. The function returns the number of elements removed.

**Call relations**: _coalesce_in calls this after grouping matching tracked changes. It depends on _changes_adjacent to avoid merging changes that only look related but are separated by real content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This answers a safety question: are two tracked-change elements actually next to each other, with only whitespace or comments between them? It prevents unrelated edits from being merged by mistake.

**Data flow**: It receives two XML elements. It checks that the first has a parent, finds both elements among that parent’s children, looks at any text and intervening nodes between them, and returns true only when nothing meaningful separates them. If the elements are missing from the parent or separated by real content, it returns false.

**Call relations**: _merge_change_run calls this before combining two tracked-change elements. Its yes-or-no result controls whether the merge is safe.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`domain_logic` · `document editing / one-shot CLI run`

A DOCX file is really a zip package full of XML files. A visible Word comment is not stored in just one place: Word expects several comment-related files, plus relationship and content-type entries that tell Word those files exist. This script does that boilerplate work so a user or another tool can add comments without manually editing every supporting file.

The main flow starts with an unpacked DOCX directory and a CommentSpec, which contains the comment id, text, author details, and optional parent comment id for replies. If this is the first comment, the script copies template comment files into the DOCX’s word folder and registers them in the package metadata. It then creates four linked XML records: the comment text, extra threaded-comment data, a stable id mapping, and a timestamped extensibility record.

One important boundary: this script does not place the visible comment anchors into document.xml. Instead, the command-line mode prints the exact XML markers the user should insert around the annotated text. Also, the comment text is expected to already be safe for XML, so special characters like ampersands must be escaped before calling it.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character uppercase hexadecimal tag used as a Word paragraph or durable comment identifier. Word uses these ids to connect the different comment support files to the same logical comment.

**Data flow**: It takes no input. It chooses a random number in Word’s expected range, formats it as eight hexadecimal characters, and returns that string.

**Call relations**: insert_comment calls this when starting a new comment. The returned values are then handed to the XML-building helpers so the comment, its extended data, and its durable id all point to matching identifiers.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces curly quotation marks and apostrophes with XML character references. This helps preserve those characters safely when XML is written back to disk.

**Data flow**: It receives a text string. It scans for the four common curly quote characters, replaces each with its numeric XML form, and returns the changed string.

**Call relations**: _serialize_xml calls this after turning an XML tree into text. It is part of the final clean-up step before updated XML is saved by _append_element_to_file.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an in-memory XML tree that the script can search or modify. This is the basic doorway from files into editable XML objects.

**Data flow**: It receives a file path. It reads the file’s bytes, parses those bytes as XML, and returns the root XML element.

**Call relations**: _append_element_to_file uses it before adding a new child element. _ensure_registrations uses it to inspect package metadata files. _resolve_parent_paragraph uses it to find the paragraph id of an existing parent comment.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an in-memory XML tree back into bytes ready to write to disk. It also applies the script’s curly-quote escaping rule before saving.

**Data flow**: It receives the root XML element. It serializes the tree with an XML declaration and UTF-8 encoding, converts curly quotes to XML references, and returns UTF-8 bytes.

**Call relations**: _append_element_to_file calls this after it has added a new XML node. In that flow, _parse_xml_file opens the file, the caller appends a child, and _serialize_xml prepares the updated result for writing.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main Word comment XML element that contains the comment id, author, date, initials, and visible comment text. This is the central record Word uses to show the comment body.

**Data flow**: It receives the comment id, author name, initials, timestamp, paragraph id, and comment text. It creates a nested XML structure with a comment paragraph, a Word comment-reference marker, and a text run containing the comment body, then returns that XML element.

**Call relations**: insert_comment calls this after generating ids and a timestamp. The returned element is passed to _append_element_to_file, which adds it to word/comments.xml.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the XML record that stores extra Word comment information, especially whether the comment is a reply to another comment. This supports modern Word’s threaded comment behavior.

**Data flow**: It receives the new comment’s paragraph id and, if this is a reply, the parent comment’s paragraph id. It creates a commentEx XML element marked as not done, adds the parent link when present, and returns it.

**Call relations**: insert_comment calls this after the main comment has been written and, for replies, after _resolve_parent_paragraph has found the parent’s paragraph id. The element is then appended to commentsExtended.xml.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML record that connects a comment paragraph id to a durable id. A durable id is a stable-looking identifier Word uses across its newer comment files.

**Data flow**: It receives the comment’s paragraph id and durable id. It creates a commentsIds XML element containing both values and returns it.

**Call relations**: insert_comment calls this once the ids have been generated. The returned element is appended to commentsIds.xml so Word can match the comment across related files.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the XML record that stores newer Word comment metadata, including the durable id and UTC timestamp. This helps the comment package look like one produced by modern Word.

**Data flow**: It receives a durable id and timestamp. It creates a commentExtensible XML element with those attributes and returns it.

**Call relations**: insert_comment calls this near the end of the add-comment flow. The result is appended to commentsExtensible.xml, alongside the other support records for the same comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the internal paragraph id for an existing parent comment. Replies need this value because Word links threaded replies by paragraph id, not just by the visible comment id.

**Data flow**: It receives the path to comments.xml and a parent comment id. It parses the file, searches comment elements for the matching id, then looks inside that comment for a paragraph id and returns it; if none is found, it returns nothing.

**Call relations**: insert_comment calls this only when the new comment is meant to be a reply. Its result is handed to _build_extended_element so the reply can point back to the parent comment.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to the end of an existing XML file and saves the file. This is the common write step used for each comment support file.

**Data flow**: It receives a file path and a child XML element. It parses the current file, appends the child to the root, serializes the updated tree, and writes the bytes back to the same path.

**Call relations**: insert_comment uses this repeatedly: first for comments.xml, then for commentsExtended.xml, commentsIds.xml, and commentsExtensible.xml. It relies on _parse_xml_file for reading and _serialize_xml for preparing the updated XML.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure the DOCX package knows about the comment files. Without these relationship and content-type entries, Word may ignore the files even if they exist.

**Data flow**: It receives the unpacked DOCX base directory. It opens document.xml.rels to add missing relationships from the main document to each comment file, choosing new rId numbers after the existing ones. It also opens [Content_Types].xml to add missing content-type declarations for those files, then writes the changed metadata back.

**Call relations**: insert_comment calls this only when it is creating the first comment files from templates. It prepares the DOCX package metadata before the script starts appending the new comment records.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds one comment or reply to the supporting XML files of an unpacked DOCX directory. This is the main function other code would call when it wants the package-level comment data created.

**Data flow**: It receives the unpacked DOCX path and a CommentSpec. It checks that the word folder exists, creates random ids and a UTC timestamp, copies template comment files if this is the first comment, registers those files in DOCX metadata, builds the four XML records for the comment, appends them to their files, and returns the new paragraph id plus a success or error message. If a requested parent comment cannot be found, it returns an error, although the main comments.xml entry has already been appended by that point.

**Call relations**: The command-line block builds a CommentSpec from user arguments and calls insert_comment. Inside, insert_comment coordinates all helper functions: it gets ids from _make_hex_tag, creates XML nodes with the _build_* helpers, asks _resolve_parent_paragraph for reply threading, and uses _append_element_to_file to write each result. On the first comment, it also calls _ensure_registrations so Word can discover the new comment files.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`entrypoint` · `manual document packaging / command-line run`

A DOCX file is really a ZIP archive full of XML files and related parts. This file is a small command-line tool for taking a folder that was previously unpacked from a DOCX and rebuilding it into a real .docx file again. Without this, edited document folders would stay as loose files and could not be opened normally by Word or other DOCX readers.

The main flow is simple. First, it checks that the input is a directory and that the output filename ends in .docx. Then it copies the whole input folder into a temporary staging area, like making a clean workbench copy so the original files are not changed. Next, it walks through the staged copy and cleans XML and relationship files by removing formatting-only whitespace. It is careful not to remove whitespace inside actual document text, because spaces in text can change what the reader sees.

Finally, it creates the output .docx as a compressed ZIP file and adds every staged file into it with paths relative to the document root. If the script is run directly from the command line, it reads the two paths from arguments, prints the result message, and exits with an error code if packing failed.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing function. It takes a folder that represents the inside of a DOCX file, cleans its XML files, and writes a new .docx archive.

**Data flow**: It receives an input directory path and an output file path. It first checks that the input exists as a folder and that the output name ends with .docx; if not, it returns no file path plus an error message. If the paths are valid, it copies the input folder into a temporary staging folder, asks _strip_xml_whitespace to clean each XML and .rels file there, creates the output folder if needed, writes all staged files into a compressed DOCX ZIP archive, and returns the finished output path plus a success message.

**Call relations**: When the script is used from the command line, this function is the main worker called after argument parsing. During its work it calls _strip_xml_whitespace for each XML-like file before handing the staged files to the ZIP writer, so the final archive is both validly packaged and whitespace-cleaned.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there for formatting the XML itself. It avoids touching text-bearing tags where spaces may be part of the actual document content.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through each element, skips real text elements such as Word text runs, removes blank-only text and tail whitespace elsewhere, removes unusual callable-tag child nodes, then writes the cleaned XML back to the same file with a UTF-8 XML declaration. If parsing or writing fails, it prints an error to standard error and raises the problem again.

**Call relations**: pack_docx calls this helper while preparing the temporary staging copy of the document. Its cleaned file is then picked up by pack_docx and placed into the final DOCX archive, so this function acts as the cleanup step before compression.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).
