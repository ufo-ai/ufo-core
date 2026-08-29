# Word DOCX editing and packaging scripts  `stage-11.3.4`

This stage is a set of behind-the-scenes tools for working on Microsoft Word documents without using Word directly. A .docx file is really a zipped package of XML files, which are text files that describe the document’s words, styles, comments, and settings. These scripts open that package, make focused changes, and close it again.

The unpack script is the “open the box” step. It expands a .docx into a folder and cleans the main document XML so later edits are easier to compare. The comment script works inside that unpacked folder. It adds a Word comment by updating all the XML pieces Word expects, not just the visible text. The pack script is the “close the box” step. It turns the folder back into a working .docx and tidies whitespace to keep the package neat. Separately, the accept changes script makes a copy of a document and asks LibreOffice to accept all tracked changes invisibly, useful for producing a clean final version.

## Files in this stage

### Tracked-change cleanup
Automates creation of a cleaned DOCX copy by accepting all tracked changes through LibreOffice.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `on-demand document cleanup, either from the command line or another caller`

This file solves a practical document-cleanup problem: a .docx file may contain tracked edits, and the system needs a final version where those edits are accepted. Instead of trying to edit the Word file format directly, the script asks LibreOffice to do the job, because LibreOffice already knows how to safely interpret and save Word documents.

The script first checks that the input exists and is really a .docx file. It then copies the input to the requested output path, so the original file is not changed. Next it prepares a temporary LibreOffice user profile and installs a small LibreOffice Basic macro there. A macro is like a tiny recorded office command; this one tells LibreOffice to run “Accept All Tracked Changes,” save the document, and close it.

LibreOffice is started in headless mode, meaning it runs without showing a window. The script also forces a simple non-graphical display backend so it can run on servers. One important quirk is handled deliberately: LibreOffice may hang even after it has already saved the cleaned file. Because of that, a timeout while running the macro is treated as success, not failure.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This builds the environment settings used when starting LibreOffice. It tells LibreOffice to use a headless-friendly display mode so it can run on machines without a normal desktop screen.

**Data flow**: It starts with the current process environment, copies all existing variables, then adds or overwrites the LibreOffice display setting. It returns that environment dictionary for later subprocess calls.

**Call relations**: When the script needs to start LibreOffice, both _ensure_macro and accept_tracked_changes call this helper first. They pass its result into subprocess.run so LibreOffice starts with the right non-graphical setup.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This creates the command-line argument that tells LibreOffice which temporary user profile to use. A separate profile keeps this script’s macro and settings away from the user’s normal LibreOffice setup.

**Data flow**: It reads the fixed profile directory path and turns it into LibreOffice’s expected UserInstallation argument string. The result is a single command-line option ready to be included in a LibreOffice command.

**Call relations**: Both _ensure_macro and accept_tracked_changes call this when building LibreOffice commands. It makes sure the initialization step and the actual macro run use the same temporary profile.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure the LibreOffice macro needed to accept tracked changes is installed in the temporary profile. Without this macro, LibreOffice would open the file but would not know which document command to run.

**Data flow**: It checks whether the macro file already exists and contains the expected macro name. If not, it starts LibreOffice briefly to create the profile folders, creates the macro directory if needed, writes the macro XML file, and returns True to show the setup is ready.

**Call relations**: accept_tracked_changes calls this before trying to clean the document. Inside, it uses _profile_arg to point LibreOffice at the temporary profile, _soffice_env to start LibreOffice safely in headless mode, and subprocess.run to perform the one-time profile initialization.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main work function: it takes an input .docx file, creates an output copy, and asks LibreOffice to accept all tracked edits in that copy. It returns a message saying either what succeeded or what went wrong.

**Data flow**: It receives two file paths: the source document and the desired output document. It validates the source, creates the output folder if needed, copies the source to the output path, ensures the LibreOffice macro exists, then runs LibreOffice headlessly on the copied file. It returns None plus a human-readable status message; it changes the output file on disk but leaves the original input untouched.

**Call relations**: This is the function used by the command-line block at the bottom of the file, and it can also be called by other code. During its work it relies on _ensure_macro to prepare LibreOffice, _profile_arg and _soffice_env to build the LibreOffice command, shutil.copy2 to preserve the original while making the output copy, and subprocess.run to launch LibreOffice. If LibreOffice times out after running the macro, this function still reports success because the document is expected to have been saved already.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### DOCX package editing
Unpacks a Word document, applies comment-related XML edits, and repacks the folder into a usable DOCX file.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `on-demand DOCX unpacking from the command line or another caller`

A .docx file is really a ZIP archive full of XML files. This script opens that archive, extracts its contents, and rewrites the XML in a friendlier form. Without it, people or tools trying to inspect a Word document would have to deal with compressed files, hard-to-read XML, and Word-specific clutter that makes small document changes look much bigger than they are.

The main flow is like unpacking a suitcase and then arranging the contents neatly on a table. First, the script checks that the input exists and looks like a .docx file. Then it unzips everything into the chosen output folder. Next, it indents XML files so they are easier for humans to read. If the main Word body file, `word/document.xml`, is present, the script can do two cleanup passes: it can combine nearby tracked insertions or deletions from the same author, and it can merge neighboring Word “runs.” A run is a small stretch of text with the same formatting; Word often splits text into many tiny runs, which makes XML noisy.

Finally, it replaces curly quote characters with XML numeric entities, so those characters are preserved in an explicit, portable way. The script returns a small `UnpackResult` summary and a human-readable message. It can also be run directly from the command line, with flags to turn run merging or tracked-change coalescing on or off.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main workhorse. It checks a DOCX file, extracts it into a folder, makes the XML easier to read, optionally simplifies Word-specific editing noise, and returns a summary of what happened.

**Data flow**: It receives the input DOCX path, the output folder path, and two true-or-false options for cleanup steps. It checks the file, creates the output folder, unzips the DOCX, finds XML-like files, formats them, edits `word/document.xml` if present, replaces curly quote characters, and then returns either an `UnpackResult` plus a success message or `None` plus an error message.

**Call relations**: This function coordinates the whole file. During its run it calls `_indent_xml` for each extracted XML file, `_coalesce_tracked_changes` and `_merge_adjacent_runs` for the main Word document XML when those options are enabled, and `_replace_curly_quotes` at the end. The command-line block at the bottom calls this function and prints its message.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This makes one XML file easier to read by adding consistent spacing and line breaks. It is meant for human-friendly output, not for changing document meaning.

**Data flow**: It receives a path to an XML file. It tries to parse the file as XML, asks `lxml` to indent it with two spaces, and writes the formatted XML back to the same file. If parsing or writing fails, it quietly leaves the file alone.

**Call relations**: `unpack_docx` calls this after extracting the DOCX, once for every `.xml` and `.rels` file it finds. It does not call the document-specific cleanup helpers; it only prettifies a single file.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This rewrites curly quote characters as explicit XML character references. That keeps smart quotes visible and stable in the unpacked text form.

**Data flow**: It receives a file path, reads the file as UTF-8 text, searches for curly single or double quote characters, and replaces each one with its numeric XML entity such as `&#x201C;`. It writes the changed text back only when a replacement is needed. If anything goes wrong, it quietly leaves the file as it was.

**Call relations**: `unpack_docx` calls this near the end for every extracted XML-related file. It runs after indentation and document cleanup, so it is the final text-normalization pass.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This cleans up `document.xml` by combining neighboring Word text runs that have the same formatting. It reduces clutter created by Word splitting continuous text into many small pieces.

**Data flow**: It receives the path to `document.xml`. If the file exists, it parses the XML, removes Word proofing-error markers, deletes run attributes related to revision IDs, finds parent elements that contain runs, and asks `_merge_runs_in` to merge compatible runs inside each parent. If anything was merged, it writes the updated XML back and returns the number of runs absorbed.

**Call relations**: `unpack_docx` calls this only for the main document XML and only when run merging is enabled. It delegates the detailed run-by-run work to `_merge_runs_in`, which in turn uses `_canonical_rpr` and `_join_adjacent_text`.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This creates a stable signature for a run’s formatting. The signature lets the script decide whether two neighboring runs are formatted the same way and can safely be merged.

**Data flow**: It receives one run XML element. It looks for the run-properties child element, which stores formatting details. If there is no such child, it returns `None`; otherwise it serializes that formatting element in a canonical, normalized XML form and returns it as text.

**Call relations**: `_merge_runs_in` calls this for each run it examines. The returned signature is used to group adjacent runs with matching formatting before their content is combined.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This merges compatible runs within one parent XML element. It is the local worker that turns several adjacent same-format text chunks into one larger chunk.

**Data flow**: It receives a container XML element, such as a paragraph-like parent. It walks through the container’s direct children, groups neighboring run elements that have the same formatting signature, moves non-formatting children from later runs into the first run of each group, removes the now-empty donor runs, joins neighboring text nodes inside the merged run, and returns how many runs were absorbed.

**Call relations**: `_merge_adjacent_runs` calls this for each container that contains runs. This function calls `_canonical_rpr` to compare formatting and `_join_adjacent_text` after merging so the remaining run’s text is not unnecessarily split.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This tidies a single run after merging by combining neighboring text elements. It prevents the merged run from still containing several separate text fragments when one would do.

**Data flow**: It receives one run XML element. It scans its child elements, and whenever two neighboring children are both Word text nodes, it joins their text into the first node and removes the second. If the combined text starts or ends with a space, it marks the node so XML readers preserve that space.

**Call relations**: `_merge_runs_in` calls this after it has moved content from donor runs into an anchor run. It is the final cleanup step for each merged group of runs.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This simplifies Word tracked changes by merging consecutive insertions or deletions from the same author. That makes the XML easier to review because one continuous edit is represented as one change instead of many tiny ones.

**Data flow**: It receives the path to `document.xml`. If the file exists, it reads and parses the XML while preserving existing blank text, finds paragraph and table-cell containers, and for each container tries to merge adjacent inserted-change and deleted-change elements. If any elements were merged, it writes the updated XML back and returns the number of change elements removed.

**Call relations**: `unpack_docx` calls this before run merging when tracked-change coalescing is enabled. It calls `_coalesce_in` for each relevant container and for each change type, leaving the detailed author grouping and adjacency checks to helper functions.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This looks inside one container for tracked changes of one type, either insertions or deletions, and groups nearby changes by author. It prepares the right batches for actual merging.

**Data flow**: It receives a container XML element and a change type string such as `ins` or `del`. It selects direct child elements of that type, groups them by their Word author attribute, sends each group to `_merge_change_run`, and returns the total number of elements merged away.

**Call relations**: `_coalesce_tracked_changes` calls this while scanning paragraphs and table cells. It passes the possible merge groups onward to `_merge_change_run`, which decides whether each pair is truly adjacent.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This merges a sequence of tracked-change elements when they are next to each other in the XML. It keeps the first element and moves later change content into it.

**Data flow**: It receives a list of tracked-change XML elements, already of the same type and author group. Starting with the first as the anchor, it checks each later element with `_changes_adjacent`. If two changes are adjacent, it moves the later element’s children into the anchor, preserves any trailing text in the parent, removes the later element, and counts it as absorbed. If a later change is not adjacent, that later element becomes the new anchor.

**Call relations**: `_coalesce_in` calls this for each author group. This function relies on `_changes_adjacent` to avoid merging changes that only look related but are separated by real content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This answers a careful yes-or-no question: are two tracked-change elements effectively next to each other? It prevents the script from incorrectly merging edits that have meaningful content between them.

**Data flow**: It receives two XML elements. It checks that the first has a parent, finds both elements among that parent’s children, and examines everything between them. It allows only comments and whitespace between the two; if it finds another real element or non-blank text, it returns `False`. Otherwise it returns `True`.

**Call relations**: `_merge_change_run` calls this before combining two tracked-change elements. It acts as the safety check that keeps tracked-change coalescing from crossing real document boundaries.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `document editing / CLI invocation`

A DOCX file is really a zipped folder full of XML files. A comment in Word is not stored in just one place: Word expects the comment text, thread information, long-lasting IDs, timestamps, file relationships, and content-type declarations to all line up. This script does that behind-the-scenes paperwork.

The main job starts with an unpacked DOCX directory, a comment ID, text, and optional author or parent-comment information. If the document has never had comments before, the script copies template comment files into the word folder and registers those files so Word knows they exist. Then it creates four matching XML entries: the visible comment text, extra thread data, a durable identifier, and timestamp metadata. For a reply, it also looks up the parent comment’s paragraph ID so Word can connect the reply to the right thread.

One important limitation: this script does not place the comment markers around text in document.xml. Instead, after adding the comment records, it prints the XML markers the caller must insert around the actual annotated text. In everyday terms, this file writes the comment into Word’s filing cabinet, then tells you where to put the sticky-note pointer in the document body.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character hexadecimal label, which Word uses as an internal paragraph or durable ID. It is like making a short tracking code for one part of the comment.

**Data flow**: It takes no input. It asks for a random number in Word’s expected range, formats that number as uppercase hexadecimal text, and returns the resulting eight-character string.

**Call relations**: When insert_comment is preparing a new comment, it calls this twice: once for the comment paragraph ID and once for the durable ID. Those IDs are then passed into the XML-building functions so the different comment files can refer to the same comment consistently.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces curly quotation marks and apostrophes with XML entity text. This helps preserve those characters safely when XML is written back to disk.

**Data flow**: It receives a text string. It scans for the four common curly quote characters and replaces each one with its XML-safe entity form, then returns the changed string.

**Call relations**: _serialize_xml uses this just before saving XML. That means any XML file written through _append_element_to_file gets this extra cleanup step.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an editable XML tree. Other functions use this whenever they need to inspect or add to an existing DOCX XML file.

**Data flow**: It receives a file path. It reads the raw bytes from that file, parses those bytes as XML, and returns the root XML element that code can search or modify.

**Call relations**: _append_element_to_file uses it before adding a new node, _ensure_registrations uses it before editing relationship and content-type files, and _resolve_parent_paragraph uses it to find a parent comment’s paragraph ID.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an edited XML tree back into bytes ready to save. It also applies the curly-quote cleanup before the XML is written.

**Data flow**: It receives the root of an XML tree. It converts the tree into UTF-8 XML bytes with an XML declaration, changes curly quotes into XML entities, and returns the final bytes.

**Call relations**: _append_element_to_file calls this after adding a child element. This keeps the read-edit-write cycle for comment XML files in one consistent format.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main Word comment entry: author, date, comment ID, internal paragraph ID, and the visible comment text. This is the part that stores what the user will actually read in Word’s comment pane.

**Data flow**: It receives the comment ID, author name, initials, timestamp, paragraph tracking code, and body text. It creates a nested XML element shaped the way Word expects, including a small comment-reference run and a text run, then returns that XML element without saving it yet.

**Call relations**: insert_comment calls this after it has generated IDs and a timestamp. The returned element is then handed to _append_element_to_file so it can be added to word/comments.xml.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra XML entry Word uses for comment status and threading. For replies, it records which parent comment paragraph this reply belongs under.

**Data flow**: It receives the new comment’s paragraph ID and, if this is a reply, the parent comment’s paragraph ID. It creates a commentEx XML element marked as not done, optionally adds the parent link, and returns the element.

**Call relations**: insert_comment calls this after it has optionally resolved the parent paragraph. The returned element is appended to commentsExtended.xml so Word can understand comment threads.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML entry that connects a comment paragraph ID to a durable ID. A durable ID is a stable tracking value Word can use across newer comment features.

**Data flow**: It receives the paragraph tracking code and the durable tracking code. It places both into a commentsIds XML element and returns that element.

**Call relations**: insert_comment creates the two IDs first, then calls this function and appends the result to commentsIds.xml. This keeps the newer Word comment metadata tied to the main comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the XML entry for newer extensible comment metadata, including the durable ID and UTC timestamp. This supports modern Word comment information beyond the older comments.xml file.

**Data flow**: It receives a durable ID and a timestamp. It creates a commentExtensible XML element containing those values and returns it.

**Call relations**: insert_comment calls this near the end of the comment-writing process, then appends the result to commentsExtensible.xml so the durable ID also has modern timestamp metadata.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the internal paragraph ID for an existing parent comment. This is needed when the new comment is a threaded reply rather than a standalone comment.

**Data flow**: It receives the path to comments.xml and the parent comment’s public comment ID. It reads the XML, searches for the matching comment, looks inside it for its paragraph element, and returns that paragraph’s ID if found; otherwise it returns nothing.

**Call relations**: insert_comment calls this only when a parent comment ID was provided. If it cannot find the parent paragraph, insert_comment stops with an error instead of creating a broken reply thread.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to the end of an existing XML file and saves the file. It is the small helper that performs the actual update after another function has built the new comment-related element.

**Data flow**: It receives a file path and a child XML element. It reads and parses the file, appends the child to the root element, serializes the updated XML, and writes the bytes back to the same file.

**Call relations**: insert_comment uses this repeatedly: once for the main comment and once for each companion metadata file. It relies on _parse_xml_file to read and _serialize_xml to prepare the saved output.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure the DOCX package knows about the comment files. Without these registrations, the XML files might exist in the folder but Word would not know to load them.

**Data flow**: It receives the unpacked DOCX base directory. It checks document.xml.rels for relationships to the comment files and adds missing relationship entries with new rId values. It also checks [Content_Types].xml and adds missing content-type overrides for the comment files. It writes those XML files back if it changes them.

**Call relations**: insert_comment calls this when it is adding the first comment and has just copied the template comment files. This prepares the document package so the later appended comment data is discoverable by Word.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds a complete comment record, or reply record, to an unpacked DOCX directory. This is the main function other code or the command-line script uses to do the work.

**Data flow**: It receives the path to an unpacked DOCX folder and a CommentSpec containing the comment ID, text, author details, and optional parent ID. It checks for the word folder, creates tracking IDs and a timestamp, copies template files if this is the first comment, registers those files, builds the needed XML entries, appends them to the right files, and returns the new paragraph ID plus a success or error message.

**Call relations**: This is the hub of the file. It calls the ID maker, the XML builders, the parent lookup helper, the file appender, and the registration helper in the order needed to produce a valid Word comment. The command-line block at the bottom creates a CommentSpec, calls insert_comment, prints the result, and then prints instructions for adding the matching markers to document.xml.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`io_transport` · `document packaging / command-line use`

A DOCX file is really a ZIP archive with a specific set of folders and XML files inside it. This script is the “put it back in the box” tool: after someone has unpacked and edited those files, it copies them to a temporary staging area, cleans up unnecessary whitespace in XML files, and zips everything into a new .docx file.

The script first checks two simple things: the input must be a real directory, and the output name must end in .docx. It then works in a temporary folder so it does not accidentally damage the original unpacked document. Inside that copy, it looks for XML files and relationship files, which are the files that describe the document content and how parts of the document connect to each other.

The whitespace cleanup is careful. It removes indentation-only text between XML elements, but it avoids text-bearing Word tags such as normal text and deleted text. That matters because spaces inside real document text can change what the user sees. Finally, the staged folder is written into a compressed ZIP archive with a .docx extension. If used from the command line, it prints a success or error message and exits with a failure code when packing was not possible.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This function takes a folder that contains the unpacked parts of a DOCX document and builds a .docx file from it. It protects the original folder by doing its cleanup work on a temporary copy.

**Data flow**: It receives an input directory path and an output file path. It checks that the input is a directory and that the output ends in .docx; if either check fails, it returns no file path and an error message. Otherwise, it copies the directory into a temporary staging folder, asks _strip_xml_whitespace to clean each XML and relationship file, creates the output folder if needed, writes all staged files into a compressed DOCX archive, and returns the output path plus a success message.

**Call relations**: This is the main packing routine. The command-line block at the bottom uses it when someone runs the script directly. During its work, it calls _strip_xml_whitespace for each XML-like file before handing the cleaned staging folder to Python’s ZIP-writing tools to create the final document.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only formatting for the XML itself, not visible document text. It keeps whitespace inside Word text tags so the document’s actual words and spacing are not accidentally changed.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through every element, skips elements that contain real Word text, removes blank-only text and blank-only tails around child elements, removes special non-element children when needed, and writes the cleaned XML back to the same file. If parsing or writing fails, it prints an error message to standard error and raises the problem again so packing stops instead of silently producing a bad file.

**Call relations**: pack_docx calls this helper while preparing the temporary staging copy of the document. Its output is not a separate value; instead, it updates the staged XML file in place, and pack_docx later includes that cleaned file in the final .docx archive.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).
