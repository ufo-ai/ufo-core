# Word DOCX packaging and comment helpers  `stage-10.4.2`

This stage is a set of behind-the-scenes command-line tools for working with Word DOCX files. A DOCX file is really a zipped package of XML files, where XML is structured text that describes the document. The tools let the system open that package, make safe edits, and close it again.

The usual flow starts with accept_changes.py when a document has tracked edits. It asks LibreOffice to run invisibly in the background and save a clean version with all changes accepted. Next, unpack.py opens the DOCX package into a folder and tidies Word’s noisy XML so people or other tools can read and change it more reliably. If comments need to be added, comment.py creates or updates the hidden Word comment records and relationship files that Word requires, though another step must still place the visible comment markers in the main document XML. Finally, pack.py zips the folder back into a usable DOCX and removes extra XML spacing to keep the file neat.

## Files in this stage

### Document cleanup
Prepares a DOCX by accepting tracked changes through headless LibreOffice before lower-level package editing.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `document processing`

This file solves a practical document-cleanup problem: a .docx file may contain tracked edits, and the system needs an output file where those edits have all been accepted. Instead of trying to edit the Word file format directly, the script asks LibreOffice to do the work, much like asking a word processor to click “Accept All Changes” and save.

The script first checks that the input file exists and really looks like a .docx file. It then copies the original to the requested output path, so the source file is not changed. Next, it makes sure LibreOffice has a small Basic macro installed in a temporary LibreOffice user profile. A macro is a tiny script that runs inside LibreOffice; here it tells LibreOffice to accept all tracked changes, save the document, and close it.

Finally, the script starts LibreOffice with that macro and the copied document. One important detail is that LibreOffice may hang even after it has already saved the file. Because of that, a timeout is treated as success rather than failure. Without this file, the project would need another way to reliably remove tracked changes from Word documents before using or returning them.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This prepares the environment variables used when starting LibreOffice. It tells LibreOffice to use a non-visual display backend, which helps it run safely in the background without a desktop window.

**Data flow**: It starts with a copy of the current process environment. It adds or replaces one setting, SAL_USE_VCLPLUGIN, with the value svp. It returns the updated environment dictionary for later LibreOffice subprocess calls.

**Call relations**: When the script needs to start LibreOffice, both _ensure_macro and accept_tracked_changes call this helper first. They pass its returned environment into subprocess.run so LibreOffice runs in the intended headless-friendly mode.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This builds the command-line option that points LibreOffice at the temporary user profile used by this script. A LibreOffice profile is where LibreOffice stores user-specific settings and macros.

**Data flow**: It reads the fixed PROFILE_DIR path and formats it into LibreOffice’s expected -env:UserInstallation=file://... argument. The output is a single string that can be placed into a LibreOffice command.

**Call relations**: Both _ensure_macro and accept_tracked_changes use this helper when launching LibreOffice. This keeps the macro installation and the actual document processing pointed at the same temporary LibreOffice profile.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure the LibreOffice macro needed to accept tracked changes is installed before any document is processed. It is like checking that the right tool is in the toolbox before starting the job.

**Data flow**: It checks whether the expected macro file already exists and contains the AcceptAllTrackedChanges macro. If not, it starts LibreOffice briefly to initialize the profile, creates the macro folder if needed, and writes the macro XML file. It returns True once the macro file is in place.

**Call relations**: accept_tracked_changes calls this after copying the input document but before running LibreOffice on it. Inside, _ensure_macro uses _profile_arg to choose the temporary LibreOffice profile, _soffice_env to set the subprocess environment, and subprocess.run to let LibreOffice initialize that profile if necessary.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main work function: it creates an output .docx file with all tracked changes accepted. Other code, or the command-line wrapper at the bottom of the file, can use it to clean up a document.

**Data flow**: It receives an input file path and an output file path. It turns them into Path objects, checks that the input exists and has a .docx extension, creates the output folder, and copies the input file to the output location. Then it ensures the LibreOffice macro exists and launches LibreOffice on the copied file. It returns a pair where the first value is always None and the second value is a human-readable success or error message. It also changes the filesystem by creating directories, copying the file, installing the macro if needed, and saving the modified output document.

**Call relations**: This function is the center of the script’s flow. It calls _ensure_macro to prepare LibreOffice, then uses _profile_arg and _soffice_env when starting LibreOffice for the real document conversion work. It also relies on pathlib.Path for path handling, shutil.copy2 to preserve and copy the source file, and subprocess.run to invoke the external soffice command. If LibreOffice times out, this function still reports success because the macro often completes and saves before LibreOffice stops responding.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### DOCX package editing
Unpacks a DOCX into editable XML, adds Word comment support files, and repacks the folder into a finished DOCX.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual DOCX unpacking and preparation before XML editing`

A .docx file is really a ZIP archive full of XML files. This file is a small command-line tool that opens that archive, extracts its contents, and makes the XML easier to inspect and edit. Without this step, someone working on a Word document at the XML level would face long, cramped XML lines and many tiny Word fragments that make changes hard to understand.

The main path is simple: check that the input exists and looks like a .docx file, unzip it into the chosen output folder, pretty-print the XML, then do a few cleanups. The most important cleanup happens in word/document.xml, the main document body. Word often splits text into many adjacent “runs” — small pieces of text with formatting. If neighboring runs have the same formatting, this script can merge them, like joining torn strips of the same sentence back together. It can also combine neighboring tracked insertions or deletions from the same author, so change history is less fragmented.

Finally, it replaces curly quote characters with XML character references. The script can be used from Python through unpack_docx, or run directly from the command line with flags to turn run merging or tracked-change coalescing on and off.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker for the script. It validates the requested .docx file, unzips it, formats the extracted XML, optionally simplifies Word’s document XML, and returns both a structured result and a human-readable message.

**Data flow**: It receives an input file path, an output directory path, and two yes-or-no options. It checks the file, creates the output folder, extracts the ZIP contents, finds XML-style files, rewrites them in a readable form, optionally cleans word/document.xml, escapes curly quotes, and then returns an UnpackResult with counts plus a summary message. If the input is missing, is not a .docx, or is not a valid ZIP archive, it returns no result and an error message instead.

**Call relations**: This function is the conductor for the whole file. When the script is run from the command line, the bottom of the file parses the user’s arguments and calls unpack_docx. During its work it calls _indent_xml for readable formatting, _coalesce_tracked_changes for tracked edits, _merge_adjacent_runs for Word run cleanup, and _replace_curly_quotes for final XML-safe text cleanup.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This function makes one XML file easier for humans to read by adding line breaks and indentation. It is a cleanup step after the .docx archive has been extracted.

**Data flow**: It receives the path to one XML file. It tries to parse the file as XML, asks the XML library to indent the tree with two spaces, and writes the formatted XML back to the same file. If parsing or writing fails, it quietly leaves that file unchanged.

**Call relations**: unpack_docx calls this for every extracted .xml and .rels file. It runs early, before the more document-specific cleanup, so the unpacked folder starts out readable.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This function replaces curly quotation marks and apostrophes with explicit XML character references. That keeps those characters visible and stable in the text form of the XML.

**Data flow**: It receives the path to a file, reads it as UTF-8 text, checks whether it contains curly quotes, and if so writes the same text back with each curly quote replaced by an entity such as &#x201C;. If reading or writing fails, it silently skips the file.

**Call relations**: unpack_docx calls this near the end for every extracted XML-style file. It is a final pass after indentation, tracked-change coalescing, and run merging have already done their work.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word’s main document XML by merging neighboring text runs that have the same formatting. A run is Word’s small unit of formatted text, and Word often creates many more of them than a human would expect.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns 0. Otherwise it parses the XML, removes proofing-error markers, removes revision-ID-style run attributes, finds the parent containers that contain runs, asks _merge_runs_in to combine compatible neighbors, writes the XML back only if something changed, and returns the number of runs absorbed into other runs.

**Call relations**: unpack_docx calls this when run merging is enabled and document.xml exists. It delegates the actual within-container merging to _merge_runs_in, while it takes care of loading the document, finding the right places to inspect, and saving the result.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This function creates a normalized fingerprint of a run’s formatting. The script uses that fingerprint to decide whether two neighboring Word runs are formatted the same way and can be safely joined.

**Data flow**: It receives one run XML element. It looks for that run’s formatting child, called rPr in WordprocessingML, and if it exists converts it into a canonical string form. If the run has no formatting child, it returns None.

**Call relations**: _merge_runs_in calls this for each run it examines. Its answer becomes the comparison key that tells _merge_runs_in whether the current run belongs with the previous run or starts a new group.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This function does the actual work of joining neighboring Word runs inside one parent XML element. It only merges runs that are side by side and have the same formatting fingerprint.

**Data flow**: It receives an XML container, such as a paragraph-like parent. It walks through the container’s children, groups consecutive run elements with matching formatting, and for each group keeps the first run as the anchor. It moves the non-formatting children from later runs into the anchor, removes the now-empty donor runs, joins neighboring text pieces inside the anchor, and returns how many donor runs were removed.

**Call relations**: _merge_adjacent_runs calls this once for each container that contains runs. It relies on _canonical_rpr to compare formatting and calls _join_adjacent_text after merging so the text inside the surviving run is not left split into unnecessary pieces.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This function cleans up inside a single run after other runs have been merged into it. If two text elements now sit next to each other, it combines them into one text element.

**Data flow**: It receives one run XML element. It scans that run’s child elements from left to right; whenever two neighboring children are both text elements, it joins their text, updates the XML space-preservation flag if the combined text starts or ends with a space, and removes the second text element. It changes the run in place and returns nothing.

**Call relations**: _merge_runs_in calls this after it has moved content from donor runs into an anchor run. This is the final tidy-up step that turns a mechanically merged run into cleaner XML.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word tracked changes by combining neighboring insertions or deletions from the same author. It reduces clutter without changing the actual inserted or deleted content.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns 0. Otherwise it reads and parses the XML while preserving blank text, finds paragraph and table-cell containers, and for each container asks _coalesce_in to combine adjacent insertion and deletion elements. If any elements were absorbed, it writes the updated XML back and returns the number of absorbed tracked-change elements.

**Call relations**: unpack_docx calls this when tracked-change coalescing is enabled. It breaks the job into smaller passes by container and change type, handing each pass to _coalesce_in.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This function looks inside one XML container for tracked changes of one kind, either insertions or deletions, and groups nearby changes by author. It prepares those groups for safe merging.

**Data flow**: It receives an XML container and a change type name such as ins or del. It collects the matching direct child elements; if there are fewer than two, it returns 0. Otherwise it groups consecutive matching elements with the same author value, asks _merge_change_run to combine each group where possible, and returns the total number of absorbed elements.

**Call relations**: _coalesce_tracked_changes calls this for each paragraph or table cell and for both insertion and deletion changes. It hands each same-author group to _merge_change_run, which performs the adjacency checks and actual XML movement.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This function merges a same-author sequence of tracked-change elements when the elements are truly adjacent in the document. It preserves the children of later changes by moving them into the first compatible change element.

**Data flow**: It receives a list of tracked-change XML elements. If the list has fewer than two elements, it returns 0. Starting with the first element as the anchor, it checks each later element with _changes_adjacent; when they are adjacent, it moves the later element’s children into the anchor, preserves any trailing text in the parent, removes the later element, and counts it as absorbed. If a later element is not adjacent, it becomes the new anchor. The function returns the number of removed change elements.

**Call relations**: _coalesce_in calls this after grouping tracked changes by author. This function depends on _changes_adjacent to avoid merging changes that only look related but are separated by real content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This function answers one safety question: are two tracked-change XML elements next to each other, with only whitespace or comments between them? It prevents the script from accidentally joining changes that are separated by meaningful document content.

**Data flow**: It receives two XML elements. It finds their shared parent, locates both elements among that parent’s children, gathers any text between them, and rejects the pair if a real non-comment element sits in the middle. It returns true only when the space between them is blank after trimming; otherwise it returns false.

**Call relations**: _merge_change_run calls this before merging one tracked-change element into another. Its answer controls whether merging is safe or whether the later change must remain separate and become the next anchor.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `one-shot document editing script`

A DOCX file is really a ZIP archive full of XML files. Adding a comment is not just adding one line of text: Word stores comment text, thread information, stable IDs, dates, relationships, and content-type registrations in several different files. This script does that boilerplate work for an already-unzipped DOCX directory.

The main job is done by insert_comment. It checks that the unpacked document has a word folder, makes random hexadecimal IDs that Word uses internally, and records the current UTC time. If this is the first comment, it copies template comment files into the document and registers them so Word knows they exist. Then it appends matching XML entries to comments.xml, commentsExtended.xml, commentsIds.xml, and commentsExtensible.xml. These files act like separate ledgers for the same comment: one stores the visible text, one stores reply/thread state, one maps paragraph IDs to durable IDs, and one stores extra date information.

If the comment is a reply, the script looks up the parent comment’s paragraph ID so Word can connect the thread. One important detail: the caller must pass comment text that is already safe for XML, for example using &amp; instead of a raw ampersand. After running as a command-line script, it prints the XML marker snippets that still need to be inserted into document.xml around the text being annotated.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character hexadecimal label, such as the paragraph and durable IDs Word uses to tie comment records together. It gives each new comment internal tags that are unlikely to collide.

**Data flow**: It takes no input. It asks Python’s random number generator for a number in a fixed range, formats that number as uppercase hexadecimal text padded to eight characters, and returns that string.

**Call relations**: insert_comment calls this when starting a new comment. The returned values are then passed into the XML-building helpers so the different comment files all refer to the same new comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces smart curly quote characters with XML character references. This helps preserve those characters safely when XML is written back to disk.

**Data flow**: It receives a text string. It scans for the four curly quote characters and replaces each one with its numeric XML form, then returns the changed string.

**Call relations**: _serialize_xml calls this just before XML bytes are written out. It is a final cleanup step in the file-writing path used by _append_element_to_file.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an XML tree that the rest of the script can inspect or modify. This is the common doorway from stored DOCX XML into editable in-memory XML.

**Data flow**: It receives a file path. It reads the file’s raw bytes, parses those bytes with lxml, and returns the root XML element.

**Call relations**: _append_element_to_file uses it before adding a new child element. _ensure_registrations uses it to inspect relationship and content-type files. _resolve_parent_paragraph uses it to search existing comments.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an edited XML tree back into bytes suitable for saving to a file. It also applies the curly-quote escaping rule before the bytes are written.

**Data flow**: It receives the root of an XML tree. It serializes the tree with an XML declaration and UTF-8 encoding, converts curly quotes to XML character references, and returns the final bytes.

**Call relations**: _append_element_to_file calls this after adding a new XML child. Together, parsing, appending, serializing, and writing form the basic update cycle for the comment files.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main comment XML entry that stores the comment’s ID, author, date, initials, internal paragraph ID, and visible text. This is the part Word reads as the actual comment body.

**Data flow**: It receives the public comment ID, author details, timestamp, generated paragraph ID, and comment text. It creates a w:comment XML element with a paragraph, a comment-reference run, and a text run, then returns that element.

**Call relations**: insert_comment calls this after preparing IDs and a timestamp. The returned element is handed to _append_element_to_file so it can be added to comments.xml.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra XML entry Word uses for comment status and threading. For replies, it records which parent paragraph this comment belongs under.

**Data flow**: It receives the new comment’s paragraph ID and, optionally, the parent comment’s paragraph ID. It creates a commentsExtended XML element marked as not done, adds the parent link if present, and returns it.

**Call relations**: insert_comment calls this after it has either found the parent paragraph ID or decided this is a top-level comment. The result is appended to commentsExtended.xml.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML entry that connects a comment paragraph ID to a durable ID. A durable ID is a stable internal identifier Word can use beyond the visible comment number.

**Data flow**: It receives the generated paragraph ID and generated durable ID. It creates a commentsIds XML element containing both values and returns it.

**Call relations**: insert_comment calls this for every new comment. The returned element is appended to commentsIds.xml so Word can match the comment’s related records.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the newer Word XML entry that stores extra comment metadata, including the durable ID and UTC date. This supports modern Word comment features.

**Data flow**: It receives the durable ID and timestamp. It creates a commentsExtensible XML element with those attributes and returns it.

**Call relations**: insert_comment calls this after creating the durable ID and timestamp. The result is appended to commentsExtensible.xml to complete the set of records for the comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the internal paragraph ID for an existing parent comment. Replies need this because Word links comment threads using paragraph IDs, not just the visible comment number.

**Data flow**: It receives the path to comments.xml and the parent comment ID. It parses the XML, searches for a comment with that ID, then looks inside it for a paragraph ID and returns that value; if none is found, it returns nothing.

**Call relations**: insert_comment calls this only when the new comment is meant to be a reply. Its result is passed into _build_extended_element so the reply can point back to its parent.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to the end of an existing XML file and saves the file again. It is the script’s reusable “open, add, save” helper.

**Data flow**: It receives a file path and an XML child element. It parses the file, appends the child to the root element, serializes the updated XML, and writes the new bytes back to the same path.

**Call relations**: insert_comment calls this repeatedly, once for each comment-related XML file. It relies on _parse_xml_file to read the current file and _serialize_xml to produce the updated file contents.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure the DOCX package advertises the comment files to Word. Without these relationship and content-type entries, the XML files could exist but Word might not treat them as official parts of the document.

**Data flow**: It receives the unpacked DOCX base directory. It reads document.xml.rels to add missing relationships for the comment files, choosing new rId numbers after the existing ones, and reads [Content_Types].xml to add missing content-type overrides. It writes those files back if it adds entries.

**Call relations**: insert_comment calls this when it is setting up comments for the first time. This prepares the DOCX package before the new comment entries are appended to the individual comment XML files.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds one comment, or one threaded reply, to an unpacked DOCX directory. It is the main reusable function behind both importing this file and running it as a command-line script.

**Data flow**: It receives the unpacked document path and a CommentSpec containing the comment ID, text, author, initials, and optional parent ID. It checks for the word folder, creates internal IDs and a timestamp, copies template files and registers them if this is the first comment, builds the needed XML records, appends them to the right files, and returns the new paragraph ID plus a success or error message. If a requested parent comment cannot be found, it returns an error message.

**Call relations**: The command-line block builds a CommentSpec from user arguments and calls this function. Inside, insert_comment coordinates all helpers: it gets IDs from _make_hex_tag, builds XML with the _build_* functions, finds parent data through _resolve_parent_paragraph when needed, writes records through _append_element_to_file, and calls _ensure_registrations during first-time setup.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`io_transport` · `document packaging/export`

A DOCX file is really a ZIP archive containing many XML files and related resources. This script is the “put it back in the box” step: it takes a directory that represents an unpacked Word document, cleans the XML files, and writes a new .docx archive. Without this file, a workflow that edits DOCX contents as folders would have no simple way to rebuild the final document file that Word or other tools can open.

The main flow checks two basic things first: the input must be a directory, and the output name must end in .docx. It then copies the whole directory into a temporary staging area, like making a safe workbench copy before packing a suitcase. In that copy, it finds XML files and relationship files, then removes whitespace-only text in places where that whitespace is not meaningful. It is careful not to remove whitespace inside Word text tags, because spaces inside document text can change what the user sees.

After cleanup, it creates the destination folder if needed and writes every staged file into a compressed ZIP archive with the .docx name. If XML cleanup fails, the script reports which file failed and raises the error so the caller does not silently get a broken document.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a .docx file from an unpacked DOCX directory. It is used when the document contents have been worked on as normal files and need to become a single Word-compatible document again.

**Data flow**: It receives an input directory path and an output file path. First it checks that the input is really a folder and that the output filename ends in .docx; if not, it returns no file path and an error message. If the inputs are valid, it copies the directory to a temporary staging area, asks `_strip_xml_whitespace` to clean each XML and relationship file there, then writes all staged files into a compressed .docx archive. It returns the path to the created file and a success message.

**Call relations**: This is the main worker that other code or the command-line wrapper calls when it is time to pack a document. During its run it creates a temporary folder, copies the source files into it, calls `_strip_xml_whitespace` for cleanup, and then hands the staged files to Python’s ZIP writer to produce the final .docx file.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML-like file by removing whitespace that only exists for formatting the XML itself. It deliberately preserves whitespace inside Word text elements, where spaces may be part of the actual document content.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through the elements, skips Word text-related tags, removes empty formatting text and tails where safe, removes unusual callable-tag children, and then writes the cleaned XML back to the same file using UTF-8 with an XML declaration. If parsing or writing fails, it prints an error message to standard error and raises the exception.

**Call relations**: This function is called by `pack_docx` while the document is still in the temporary staging area. Its cleaned output becomes the version that `pack_docx` later places into the final DOCX ZIP archive.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).
