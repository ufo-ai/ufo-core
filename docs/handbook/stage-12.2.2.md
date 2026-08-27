# DOCX Package Editing and Commenting  `stage-12.2.2`

This stage is behind-the-scenes support for working with Word documents as editable packages. A DOCX file is really a compressed folder, like a zipped box, containing many XML files. XML is structured text that stores the document’s words, comments, settings, and links.

The unpack script opens that box. It turns the DOCX into a folder of readable XML files and cleans up Word’s extra clutter so later tools can inspect or change it more reliably. The comment script works inside that unpacked folder. It creates the hidden XML records Word needs for a new comment or a reply in a comment thread, then tells the user which marker tags still need to be inserted around the commented text in the main document. The pack script closes the box again, rebuilding the folder into a normal DOCX file while keeping the visible document unchanged. Separately, the accept_changes script uses LibreOffice without a visible window to make a clean copy where all tracked edits have been accepted.

## Files in this stage

### Tracked-change cleanup
Accept tracked changes in a DOCX through headless LibreOffice to produce a clean document before further package editing.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `on-demand document cleanup`

This file solves a practical document-cleanup problem: a DOCX file may contain tracked edits, and another part of the system may need the final version where every proposed edit has been accepted. Python libraries do not reliably support this kind of Word-specific editing, so the script asks LibreOffice to do it instead.

The script first checks that the input exists and is a DOCX file. It then copies the input to the requested output path, so the original document is not changed. Next it makes sure LibreOffice has a small Basic macro installed in a temporary LibreOffice user profile. A macro is like a short recorded instruction sheet inside LibreOffice; this one tells LibreOffice to run its built-in “Accept All Tracked Changes” command, save the document, and close it.

Finally, the script launches `soffice`, the LibreOffice command-line program, with that macro and the copied document. One important detail is that LibreOffice sometimes finishes the work but does not exit cleanly. Because of that, a timeout is treated as success: the document has usually already been saved by the macro. Without this file, the system would need a manual LibreOffice session or a less reliable custom DOCX editor to accept tracked changes.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This prepares the environment settings used when starting LibreOffice. It tells LibreOffice to use a non-graphical display backend, which helps it run safely in the background on servers.

**Data flow**: It starts with the current process environment variables, copies them, then adds `SAL_USE_VCLPLUGIN=svp`. The result is a dictionary of environment settings that can be passed to a LibreOffice subprocess.

**Call relations**: When `_ensure_macro` or `accept_tracked_changes` starts LibreOffice, they call `_soffice_env` first so the external `soffice` program runs in headless, server-friendly mode.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This builds the command-line argument that tells LibreOffice which temporary user profile to use. A separate profile keeps this script’s macro and settings away from the user’s normal LibreOffice setup.

**Data flow**: It reads the fixed profile folder path from the file’s constants and turns it into the exact `-env:UserInstallation=...` string LibreOffice expects. The output is a single command-line argument.

**Call relations**: Both `_ensure_macro` and `accept_tracked_changes` use this helper when they launch LibreOffice, so they are always pointing at the same temporary profile where the macro is installed.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure the LibreOffice macro needed to accept tracked changes is present before the document is opened. It is the setup step that gives LibreOffice the instruction it must run.

**Data flow**: It checks whether the macro file already exists and contains the expected macro name. If not, it may start LibreOffice once to initialize the temporary profile, creates the needed macro folder, and writes the macro XML file. It returns `True` once the macro file is in place.

**Call relations**: The main document function, `accept_tracked_changes`, calls `_ensure_macro` after copying the file but before asking LibreOffice to process it. During setup, `_ensure_macro` uses `_profile_arg` and `_soffice_env` to start LibreOffice with the correct profile and background-friendly environment.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main work function: it creates an output DOCX where all tracked changes have been accepted. It is used by the command-line script and can also be called from other Python code.

**Data flow**: It receives an input file path and an output file path. It checks the input, creates the output folder if needed, copies the original DOCX to the output location, installs the LibreOffice macro if needed, and then runs LibreOffice on the copied file. It returns `None` plus a human-readable message saying either what went wrong or that the changes were accepted.

**Call relations**: This function is the center of the file’s flow. It calls `_ensure_macro` to prepare LibreOffice, then uses `_profile_arg` and `_soffice_env` when launching `soffice`. When the file is run as a command-line script, the parsed input and output paths are passed here, and its message is printed for the user.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### DOCX package commenting workflow
Unpack a DOCX into editable XML, add comment metadata to the package, and repack the folder into a usable document.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual document unpacking / CLI run`

A .docx file is really a ZIP archive full of XML files. This file opens that archive, extracts it into a directory, and makes the contents more comfortable for humans and tools to inspect. Without it, people would have to manually unzip the document and work with dense, hard-to-read XML full of Word-specific clutter.

The main flow is simple: check that the input exists and is a .docx file, unzip it, pretty-print every XML-related file, then clean up the main document body. In Word XML, pieces of text are stored in “runs,” which are small chunks of text with formatting attached. Word often splits text into many tiny runs even when they look identical. This script can merge neighboring runs when their formatting matches, like joining cut-up strips of the same sentence back together. It can also combine adjacent tracked insertions or deletions from the same author, so change history is less fragmented.

Finally, it replaces curly quote characters with XML entity text. That keeps those characters explicit and stable in the saved XML. The script can be used from the command line, and the two bigger cleanup steps can be switched off with flags if exact original structure is needed.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker for unpacking and cleaning a DOCX file. It is what both the command-line script and any caller would use to turn one Word document into a folder of readable XML.

**Data flow**: It receives an input file path, an output directory path, and two yes-or-no options for cleanup. It checks the file, creates the output folder, extracts the DOCX archive, formats XML files, optionally coalesces tracked changes and merges text runs in word/document.xml, replaces curly quotes, and then returns either a result object with counts plus a summary message, or an error message.

**Call relations**: This function drives the whole process. It calls the XML formatting helper for each extracted XML-like file, then calls the tracked-change and run-merging helpers for the main document file when those options are enabled, and finally calls the curly-quote replacement helper before reporting what happened.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This makes one XML file easier to read by adding normal indentation and line breaks. It is a readability step, not a content-editing step.

**Data flow**: It receives a path to an XML file. It parses the file as XML, asks the XML library to indent it with two spaces, and writes the formatted XML back to disk. If parsing or writing fails, it silently leaves the file alone.

**Call relations**: It is called by unpack_docx right after the DOCX archive is extracted. At that point the files exist on disk, and this helper makes them suitable for human inspection before later cleanup steps run.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This replaces curly quote characters with explicit XML entity text. That makes those punctuation marks visible and stable in the XML text rather than stored as literal Unicode characters.

**Data flow**: It receives a file path, reads the file as UTF-8 text, searches for left and right curly single or double quotes, and writes the file back with each one replaced by its numeric XML entity. If no curly quotes are found, nothing is changed; if an error occurs, it silently skips the file.

**Call relations**: unpack_docx calls this near the end for every extracted XML-related file. It runs after formatting and document-specific cleanup, so the final files on disk use the escaped quote form.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This cleans up Word’s habit of splitting text into many neighboring runs that have the same formatting. A run is a small piece of Word text plus its styling; merging identical neighbors makes the XML shorter and easier to edit.

**Data flow**: It receives the path to word/document.xml. If the file exists, it parses the XML, removes proofing-error markers, deletes run attributes related to Word revision session IDs, finds each parent element that contains runs, and asks _merge_runs_in to combine compatible neighbors. If anything was merged, it writes the updated document.xml back to disk and returns the number of absorbed runs.

**Call relations**: unpack_docx calls this when run merging is enabled. It prepares the document by removing noise that would prevent clean comparison, then delegates the local, parent-by-parent merging work to _merge_runs_in.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This produces a stable text signature for a run’s formatting. It lets the script decide whether two neighboring runs are formatted the same way and can safely be joined.

**Data flow**: It receives one run XML element. It looks for that run’s formatting child element, called rPr in Word XML; if none exists, it returns None. If formatting exists, it serializes that formatting in a canonical, normalized XML form and returns it as a string.

**Call relations**: _merge_runs_in calls this while scanning runs inside a shared parent. The returned signature is used like a label: neighboring runs with the same label are grouped for merging.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This merges neighboring run elements inside one container when their formatting signatures match. It keeps the first run and moves the later runs’ text content into it.

**Data flow**: It receives an XML container element, such as a paragraph. It walks through that container’s direct children, groups consecutive run elements with matching formatting, moves non-formatting child nodes from later runs into the first run of each group, removes the emptied later runs, joins adjacent text nodes inside the remaining run, and returns how many runs were absorbed.

**Call relations**: _merge_adjacent_runs calls this for each parent element that contains runs. This helper uses _canonical_rpr to compare formatting and _join_adjacent_text to smooth out text nodes after the structural merge.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This tidies a merged run by combining text nodes that ended up side by side. It prevents a merged run from still containing unnecessary separate text pieces.

**Data flow**: It receives one run XML element. It scans its child nodes, and whenever two neighboring children are both text nodes, it combines their text into the first one, removes the second one, and updates the XML space-preservation setting when the merged text starts or ends with a space.

**Call relations**: _merge_runs_in calls this after it has moved content from several runs into one anchor run. It is the final polish step that turns a structurally merged run into a cleaner text run.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This combines adjacent tracked insertions or deletions in the main Word document when they belong to the same author. It reduces noisy change markup without removing the fact that the text was inserted or deleted.

**Data flow**: It receives the path to word/document.xml. If the file exists, it reads and parses it while preserving existing blank text, finds paragraph and table-cell containers, and asks _coalesce_in to merge neighboring insertion and deletion blocks inside each one. If anything was reduced, it writes the XML back and returns the number of merged change elements.

**Call relations**: unpack_docx calls this before run merging when tracked-change coalescing is enabled. It delegates the actual per-container and per-change-type work to _coalesce_in.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This looks inside one XML container for tracked changes of a chosen type, either insertions or deletions, and groups nearby changes by author. It prepares candidate groups for actual merging.

**Data flow**: It receives a container element and a change type name such as ins or del. It collects direct child elements of that type, groups consecutive matching elements that have the same author attribute, sends each group to _merge_change_run, and returns the total number of change elements absorbed.

**Call relations**: _coalesce_tracked_changes calls this for each paragraph or table cell, once for insertions and once for deletions. It then hands each same-author run of changes to _merge_change_run, which checks whether the elements are truly adjacent before merging them.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This merges a list of same-author tracked-change elements when they sit next to each other in the document. It keeps the first change element and moves later change contents into it.

**Data flow**: It receives a list of insertion or deletion XML elements. Starting with the first as the anchor, it checks each later element; if it is adjacent to the anchor, it moves its children into the anchor, preserves any following text by attaching it to the right nearby place, removes the later element, and counts it. If a later element is not adjacent, that element becomes the new anchor.

**Call relations**: _coalesce_in calls this after grouping changes by type and author. This function relies on _changes_adjacent to avoid merging changes that only look related but are separated by real content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This answers the safety question: are two tracked-change elements truly next to each other, with only whitespace or comments between them? It prevents the script from accidentally merging across real document content.

**Data flow**: It receives two XML elements. It checks that they share a parent, finds their positions among that parent’s children, inspects anything between them, and returns true only when the gap contains no real element content and no non-whitespace text.

**Call relations**: _merge_change_run calls this before combining two tracked-change elements. Its yes-or-no answer controls whether merging happens or whether the later change starts a new separate group.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `manual DOCX editing script run`

A DOCX file is really a zip folder full of XML files. A visible Word comment is not stored in just one place: Word expects the comment text, extra thread data, durable IDs, file relationships, and content-type records to all agree. This file does that boilerplate so a caller does not have to hand-edit several fragile XML files.

The script works on an already-unpacked DOCX directory. If the document has never had comments before, it copies starter comment XML files from a templates folder and registers those files in Word’s relationship and content-type indexes. Then it creates a new comment entry with an author, initials, timestamp, random paragraph ID, and random durable ID. For replies, it looks up the parent comment’s paragraph ID so Word can connect the reply to the right thread.

It appends four matching XML records: the actual comment text, the threading/status record, the paragraph-to-durable-ID record, and the durable-ID timestamp record. Think of these as four labels on the same box; Word needs all of them to recognize the comment properly.

One important detail: the comment text is expected to already be safe XML, such as using `&amp;` for `&`. Also, this script does not insert the visible range markers into `document.xml`; after it runs, it prints the exact marker pattern the caller should place around the annotated text.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character uppercase hexadecimal tag, used as a Word-style identifier for comment paragraphs and durable comment records. It gives each inserted comment a fresh-looking internal label.

**Data flow**: It takes no input. It chooses a random number in the allowed range, formats that number as eight hexadecimal characters, and returns the text form of that tag.

**Call relations**: When `insert_comment` starts adding a comment, it calls this helper twice: once for the comment paragraph ID and once for the durable ID. Those IDs are then passed into the XML-building helpers so the separate comment files can refer to the same new comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces curly quotation marks and curly apostrophes with XML character references. This helps preserve those characters in a form that Word’s XML can read safely.

**Data flow**: It receives a text string. It scans for four specific curly quote characters, replaces each one with its matching XML entity, and returns the changed string.

**Call relations**: `_serialize_xml` calls this just before writing XML bytes back to disk. That means any XML file saved through `_append_element_to_file` gets this final cleanup step.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an editable XML tree. Other helpers use it whenever they need to inspect or change an existing DOCX XML file.

**Data flow**: It receives a file path. It reads the raw bytes from that file, parses them with the XML library, and returns the root XML element so callers can search or modify it.

**Call relations**: `_append_element_to_file` uses it before adding a new child element, `_ensure_registrations` uses it to read Word’s index files, and `_resolve_parent_paragraph` uses it to search existing comments for a parent reply target.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an edited XML tree back into bytes that can be written to a file. It also applies the curly-quote cleanup before saving.

**Data flow**: It receives the root of an XML tree. It serializes that tree with an XML declaration and UTF-8 encoding, replaces curly quote characters with XML references, and returns the final bytes.

**Call relations**: `_append_element_to_file` calls this after it has added a new XML element. This function is the last formatting step before the changed file is written back to disk.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main Word comment XML element: author, date, initials, the internal paragraph ID, and the visible comment text. This is the record that contains what the user actually wrote.

**Data flow**: It receives the comment ID, author details, timestamp, paragraph ID, and comment body text. It creates a Word XML `<comment>` element with a paragraph, a comment-reference run, and a text run, then returns that new XML element without writing it to disk.

**Call relations**: `insert_comment` calls this after it has chosen IDs and a timestamp. The returned element is handed to `_append_element_to_file`, which adds it to `comments.xml`.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra Word XML record that says whether a comment is done and, for replies, which parent comment paragraph it belongs to. This is what helps Word show threaded comments correctly.

**Data flow**: It receives the new comment’s paragraph ID and, optionally, the parent comment’s paragraph ID. It creates a `commentEx` XML element with the new paragraph ID, marks it as not done, adds the parent reference if there is one, and returns the element.

**Call relations**: `insert_comment` calls this after resolving any parent reply information. The element is then appended to `commentsExtended.xml` so Word can understand the comment’s thread/status metadata.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML link between a comment paragraph ID and a durable ID. The durable ID is another stable label Word uses for newer comment features.

**Data flow**: It receives a paragraph ID and a durable ID. It creates a `commentId` XML element that stores both values together and returns that element.

**Call relations**: `insert_comment` calls this for every new comment. The returned element is appended to `commentsIds.xml`, keeping Word’s ID lookup file in step with the main comment entry.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the XML record for newer extensible comment data, including the durable ID and UTC timestamp. This supports newer Word comment metadata.

**Data flow**: It receives the durable ID and a timestamp. It creates a `commentExtensible` XML element with those values and returns it.

**Call relations**: `insert_comment` calls this near the end of the insertion process. The result is appended to `commentsExtensible.xml`, completing the set of matching records Word expects.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the internal paragraph ID for an existing parent comment. This is needed when the new comment is a reply, because Word links replies by paragraph ID rather than just by the visible comment number.

**Data flow**: It receives the path to `comments.xml` and a parent comment ID. It reads and parses the comments file, searches for the matching comment, then searches inside it for a paragraph ID and returns that ID if found; otherwise it returns nothing.

**Call relations**: `insert_comment` calls this only when the caller asks to create a threaded reply. Its result is passed into `_build_extended_element`; if no parent paragraph is found, `insert_comment` reports an error instead of completing the reply metadata.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one newly built XML element to the end of an existing XML file. It is the common “open, add, save” step used for each of Word’s comment support files.

**Data flow**: It receives a file path and an XML element. It reads the existing XML tree, appends the new element as a child of the root, serializes the updated tree, and writes the bytes back to the same file.

**Call relations**: `insert_comment` calls this repeatedly: once for the main comment and once for each supporting metadata file. It relies on `_parse_xml_file` to read the file and `_serialize_xml` to prepare the saved output.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure Word’s package indexes know that the comment-related XML files exist. Without these registrations, the files might be present in the folder but Word would not know to load them.

**Data flow**: It receives the unpacked DOCX base directory. It looks for the document relationship file and the content-types file, checks whether comment entries are already registered, and if not, adds relationship and content-type records for all comment-related files, then saves the changed index files.

**Call relations**: `insert_comment` calls this when it is adding the first comment files to a document. It prepares the DOCX package so the later appended comment XML files are discoverable by Word.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds a complete comment record, or reply record, to an unpacked DOCX directory. This is the main function other code or the command-line script uses to perform the edit.

**Data flow**: It receives the unpacked DOCX path and a `CommentSpec` containing the comment ID, text, author, initials, and optional parent ID. It checks for the `word` folder, creates IDs and a UTC timestamp, copies template comment files if this is the first comment, registers those files, builds the needed XML elements, appends them to the right files, and returns the new paragraph ID plus a success or error message. It changes files on disk inside the unpacked DOCX folder.

**Call relations**: The command-line block creates a `CommentSpec` from user arguments and calls this function. Inside, it coordinates all helpers: ID creation, XML element building, parent lookup for replies, package registration, and file appending. After it returns successfully, the command-line script prints marker instructions for the user to place in `document.xml`.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`entrypoint` · `document repacking command`

A DOCX file is really a ZIP archive containing many XML files and related resources. This script is the “put it back in the box” step after a DOCX has been unpacked and edited as a folder. Without it, the edited folder would not become a normal Word document again.

The main flow is simple. It first checks that the input is a real directory and that the output name ends in `.docx`. It then copies the whole folder into a temporary staging area. This matters because the script rewrites XML files while cleaning them, and using a staging copy keeps the original working folder untouched.

Next, it walks through XML files and relationship files (`.rels`, which tell Word how document parts connect). For each one, it removes whitespace that is only there for formatting the XML itself, like blank indentation between tags. But it deliberately avoids doing this inside Word text tags, because spaces there may be part of what the reader sees. This is like cleaning extra packing paper out of a box while being careful not to throw away anything printed on the actual pages.

Finally, it zips the staged files into the requested `.docx` file. The script does not fully validate whether Word will accept the document; the comment says that validation happens later on the server side.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a `.docx` file from a folder that contains the unpacked document contents. It is the main operation someone would use after editing DOCX internals and wanting a normal Word file again.

**Data flow**: It receives an input folder path and an output file path. It checks that the input is a directory and that the output ends with `.docx`; if either check fails, it returns no file path and an error message. If the checks pass, it copies the folder to a temporary staging area, asks `_strip_xml_whitespace` to clean each XML and relationship file there, creates any missing output folders, writes the staged contents into a ZIP archive using the `.docx` name, and returns the output path plus a success message.

**Call relations**: When the script is run from the command line, the command-line wrapper passes the user’s two paths into `pack_docx`. During packing, this function calls `_strip_xml_whitespace` for each XML-like file before handing the staged folder contents to the ZIP writer, because DOCX files are stored as ZIP archives.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans unnecessary whitespace out of one XML file while preserving whitespace that may be part of the document’s visible text. It exists so the packed DOCX is tidier without accidentally changing words, spaces, fields, or deleted-text records inside the Word document.

**Data flow**: It receives the path to one XML or relationship file. It reads the file as an XML tree, walks through every element, and removes text or tail whitespace that is only blank formatting between XML tags. It skips known Word text elements such as text runs and instruction text, because whitespace there can matter to the document. It also removes special non-element XML nodes when found among children. Finally, it writes the cleaned XML back to the same file using a UTF-8 XML declaration. If parsing or writing fails, it prints an error to standard error and raises the problem again.

**Call relations**: `pack_docx` calls this helper while preparing the temporary staging copy of the document folder. `_strip_xml_whitespace` does the careful XML cleanup, then returns control to `pack_docx`, which continues by adding the cleaned files to the final DOCX archive.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).
