# Word DOCX package and change/comment tools  `stage-14.6`

This stage is a small toolbox for working with Microsoft Word DOCX files behind the scenes. A DOCX file is really a zipped package of many XML files, where XML is structured text that describes the document. These tools let the system open that package, change it safely, and put it back together.

The workflow often starts with accept_changes.py, which asks LibreOffice to open the Word file invisibly and accept all tracked edits, producing a clean version. Then unpack.py turns the DOCX into a normal folder so its XML parts can be inspected or edited. It also simplifies the main document XML to make later work more predictable.

comment.py adds a Word comment to that unpacked folder. Word stores comments in several connected XML files, so the script updates those records and then tells the user what marker still must be inserted around the exact text being commented on. Finally, pack.py zips the folder back into a working DOCX file and tidies XML spacing without changing the document’s real content.

## Files in this stage

### Tracked change cleanup
Accept tracked changes in a DOCX through headless LibreOffice to produce a clean Word document before XML-level handling.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `on-demand document conversion/cleanup`

This file solves a practical document-cleanup problem: a DOCX may contain tracked edits, and another part of the system may need the final version with every edit accepted. Instead of trying to edit the DOCX format by hand, the script asks LibreOffice to do the same action a person would choose from the menu: “Accept All Tracked Changes.”

The script first checks that the input file exists and really looks like a DOCX file. It then copies the original to the requested output path, so the source document is not changed. Next it prepares a small LibreOffice Basic macro. A macro is a tiny script LibreOffice can run inside a document. This macro tells LibreOffice to accept all tracked changes, save the document, and close it.

LibreOffice needs a user profile directory where macros live, so the script creates or reuses a temporary profile under `/tmp`. It then launches `soffice`, the LibreOffice command-line program, in headless mode with that profile and the macro address.

One important quirk is handled deliberately: LibreOffice may hang after the macro has already saved the file. Because of that, a timeout is treated as success. In everyday terms, if the worker finished stamping the document but forgot to clock out, the script still counts the document as done.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when starting LibreOffice. It forces LibreOffice to use a non-graphical display backend, which helps it run safely in the background on servers.

**Data flow**: It starts with the current process environment, which is the set of settings inherited from the operating system. It adds `SAL_USE_VCLPLUGIN=svp`, telling LibreOffice not to rely on a normal desktop window system. It returns the modified environment dictionary for later process launches.

**Call relations**: Both `_ensure_macro` and `accept_tracked_changes` call this just before starting LibreOffice with `subprocess.run`. It supplies the background-friendly settings that make those LibreOffice calls less likely to fail on a machine without a visible desktop.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: Creates the command-line argument that tells LibreOffice which temporary user profile to use. This keeps the macro installation separate from any real user’s LibreOffice settings.

**Data flow**: It reads the fixed profile directory path defined near the top of the file. It formats that path into LibreOffice’s expected `-env:UserInstallation=...` argument. The result is a string that can be placed directly into a `soffice` command.

**Call relations**: Both `_ensure_macro` and `accept_tracked_changes` use this when they launch LibreOffice. `_ensure_macro` uses it while preparing the macro profile, and `accept_tracked_changes` uses it when running the macro against the copied DOCX.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the macro it needs to accept tracked changes. Without this, the later LibreOffice command would have no saved instruction telling it what to do inside the document.

**Data flow**: It checks whether the macro file already exists and already contains the expected macro name. If so, it leaves things alone. If the macro folder does not exist yet, it briefly starts LibreOffice to initialize the temporary profile, then creates the needed folder. Finally, it writes the macro XML file and returns `True` to say the macro is ready.

**Call relations**: This is called by `accept_tracked_changes` after the input file has been copied to the output location. Inside, it calls `_profile_arg` and `_soffice_env` to start LibreOffice correctly, then uses `subprocess.run` to initialize the profile when needed. It prepares the ground before the main document-cleaning LibreOffice run.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: Creates an output DOCX where all tracked changes from the input document have been accepted. This is the main reusable function for other code, and it is also what the command-line script calls.

**Data flow**: It receives an input file path and an output file path. It checks that the input exists and has a `.docx` extension. It creates the output folder if needed, copies the input document to the output path, ensures the LibreOffice macro is installed, and then runs LibreOffice on the copied file. It returns a pair whose first value is always `None` and whose second value is a human-readable success or error message. If LibreOffice times out, the function still returns a success message because the macro often saves the file before LibreOffice gets stuck.

**Call relations**: This is the center of the file’s workflow. The command-line block at the bottom parses the two file names from the user and calls this function. During its work, it calls `_ensure_macro` to prepare the macro, `_profile_arg` and `_soffice_env` to build the LibreOffice command, `shutil.copy2` to preserve the original by working on a copy, and `subprocess.run` to actually ask LibreOffice to accept the changes.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### DOCX package editing
Unpack a DOCX into editable XML, add Word comment records, and repack the folder into a usable DOCX package.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual document unpacking / CLI run`

A .docx file is really a ZIP archive: a compressed folder containing many XML files and related parts. This script opens that archive, extracts it into a normal directory, and makes the XML easier to read. Think of it like unpacking a suitcase and then neatly folding the clothes so you can see what is inside.

After extraction, the script indents XML files so their nested structure is visible. Then, for the main Word document file, it can do two cleanup passes. One pass coalesces tracked changes, meaning it joins neighboring insertions or deletions made by the same author when Word has split them into separate XML elements. Another pass merges adjacent text “runs” that have the same formatting. In Word XML, a run is a small piece of text with formatting attached; Word often creates many tiny runs, which makes editing noisy.

Finally, the script replaces curly quote characters with XML numeric entities, so the files keep those characters in an explicit, portable form. The command-line interface lets a user turn run merging or tracked-change coalescing off if they need to preserve the original structure more closely.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: Unpacks a .docx file into a directory and applies the optional XML cleanup steps. This is the main function someone would call when they want a Word document turned into editable XML files.

**Data flow**: It receives an input file path, an output directory path, and two true/false options for cleanup. It first checks that the input exists and has a .docx extension, then creates the output directory, extracts the ZIP contents there, finds XML-like files, prettifies them, optionally cleans word/document.xml, replaces curly quotes, and returns a small result object plus a human-readable summary message. If the file is missing, has the wrong extension, or is not a valid ZIP archive, it returns no result and an error message instead.

**Call relations**: This is the top-level coordinator. When the script is run from the command line, the bottom of the file parses the user’s arguments and calls this function. Inside, it delegates the detailed work to _indent_xml, _coalesce_tracked_changes, _merge_adjacent_runs, and _replace_curly_quotes, then combines their counts into the final message.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: Makes one XML file easier for humans to read by adding consistent indentation and line breaks. This does not change the meaning of the XML; it changes the layout on disk.

**Data flow**: It receives the path to one XML file. It tries to parse the file as XML, asks lxml to indent the tree with two spaces per level, and writes the formatted XML back to the same file with a UTF-8 XML declaration. If parsing or writing fails, it quietly leaves the file as it was.

**Call relations**: unpack_docx calls this for every extracted .xml and .rels file before the deeper document cleanup. It is an early readability pass, preparing the files so people and version-control diffs can make sense of them.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: Rewrites curly quote characters as XML numeric entities, such as turning a left double quote into &#x201C;. This keeps those special characters explicit in the text files.

**Data flow**: It receives the path to one XML-related file and reads it as UTF-8 text. If it finds curly single or double quotes, it replaces each one with its matching numeric entity and writes the file back. If there are no curly quotes, or if reading or writing fails, it makes no change.

**Call relations**: unpack_docx calls this after indentation and document.xml cleanup. It is the final text-level normalization step applied across all extracted XML and relationship files.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: Simplifies the main Word document XML by combining neighboring text runs that have the same formatting. This reduces the clutter Word often creates when it splits ordinary text into many tiny pieces.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns zero. Otherwise it parses the XML, removes spelling or grammar proofing markers, removes run attributes related to Word’s internal revision IDs, finds every parent element that contains runs, and asks _merge_runs_in to simplify each one. If anything was merged, it writes the updated XML back and returns how many run elements were absorbed.

**Call relations**: unpack_docx calls this when run merging is enabled. This function works at the whole-document level and hands each local group of runs to _merge_runs_in, which performs the actual combining.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: Creates a stable text signature for a run’s formatting. The script uses this signature to decide whether two neighboring runs are formatted the same way and can safely be joined.

**Data flow**: It receives one Word run XML element. It looks for that run’s formatting child element, called rPr in WordprocessingML, the XML language used by Word documents. If there is no formatting element, it returns None; otherwise it serializes that formatting in canonical form, meaning a normalized representation suitable for comparison.

**Call relations**: _merge_runs_in calls this while scanning neighboring runs. Its output is the comparison key that lets _merge_runs_in group only runs that share the same formatting.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: Combines adjacent run elements inside one parent container when their formatting matches. It preserves the first run as the anchor and moves the later runs’ content into it.

**Data flow**: It receives a single XML element that may contain Word run children. It walks through the children in order, forms groups of consecutive runs with the same formatting signature, and ignores groups of only one run. For each larger group, it appends all non-formatting child nodes from later runs into the first run, removes the now-empty later run elements from the container, joins neighboring text nodes inside the anchor, and returns the number of removed run elements.

**Call relations**: _merge_adjacent_runs calls this once for each parent element that contains runs. This function relies on _canonical_rpr to compare formatting and _join_adjacent_text to tidy up text nodes after XML children have been moved.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: Tidies a single run after merging by joining neighboring text nodes into one text node. This avoids leaving several separate pieces of plain text sitting side by side inside the same run.

**Data flow**: It receives one run element. It scans its child nodes from left to right; whenever two neighboring children are both Word text elements, it concatenates their text into the first one and removes the second. If the merged text begins or ends with a space, it marks the XML to preserve that space, because XML tools might otherwise treat edge spaces as unimportant.

**Call relations**: _merge_runs_in calls this after it has moved content from several runs into one anchor run. It is the cleanup step that makes the newly combined run compact and safe with respect to leading or trailing spaces.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: Simplifies Word’s tracked-change markup by joining neighboring insertions or deletions from the same author. This makes document edits easier to read and edit in XML form.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns zero. Otherwise it reads and parses the XML while preserving blank text, finds paragraph and table-cell containers, and for each one asks _coalesce_in to merge nearby insertion and deletion elements. If any elements were absorbed, it writes the updated XML back and returns the number of merged change elements.

**Call relations**: unpack_docx calls this when tracked-change coalescing is enabled, before run merging. It works across the document structure and delegates the local per-container work to _coalesce_in.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: Looks inside one container, such as a paragraph or table cell, and finds tracked insertions or deletions that may be mergeable. It groups candidate change elements by author before asking for actual merging.

**Data flow**: It receives a container XML element and a change type string, either insertion or deletion. It collects immediate child elements of that type, skips work if fewer than two exist, groups consecutive candidates by their Word author attribute, and passes each group to _merge_change_run. It returns the total number of change elements that were absorbed.

**Call relations**: _coalesce_tracked_changes calls this for both insertions and deletions in every paragraph and table cell it found. This function narrows the problem down to same-type, same-author stretches, then relies on _merge_change_run to check whether the elements are truly adjacent.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: Merges a run of tracked-change elements when they sit next to each other in the XML. It keeps the first change element and moves later change contents into it.

**Data flow**: It receives a list of change elements that are already candidates for merging. Starting with the first element as the anchor, it checks each later element with _changes_adjacent. If the later element is adjacent, it moves that element’s children into the anchor, preserves any trailing text by attaching it to the nearby parent or previous sibling, removes the later element, and counts it as absorbed. If a later element is not adjacent, it becomes the new anchor. The function returns the number of removed change elements.

**Call relations**: _coalesce_in calls this after grouping candidate insertions or deletions by author. This function performs the real XML surgery, using _changes_adjacent as its safety check before combining two tracked-change elements.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: Decides whether two tracked-change elements are close enough to be merged safely. It allows only whitespace and XML comments between them.

**Data flow**: It receives two XML elements. It looks up their shared parent and their positions among that parent’s children. If either element is not found, or if a non-comment element sits between them, it returns false. Otherwise it gathers the text between them and returns true only when that text is empty or whitespace after trimming.

**Call relations**: _merge_change_run calls this before moving one tracked-change element into another. It acts like a gatekeeper, preventing the script from merging changes across real intervening content.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `manual document-editing step`

A DOCX file is really a zipped folder full of XML files. Adding a comment is not as simple as writing one line: Word stores the comment text, reply/thread information, stable IDs, timestamps, file relationships, and content-type declarations in separate places. This file is the helper that does that boilerplate safely enough for a document-editing workflow.

The main public action is `insert_comment`. It checks that the unpacked document has a `word` folder, creates random-looking hexadecimal IDs used by Word, and records the current UTC time. If this is the first comment in the document, it copies template comment files into place and registers those files in the DOCX package metadata. It then appends new XML entries to `comments.xml`, `commentsExtended.xml`, `commentsIds.xml`, and `commentsExtensible.xml`.

For replies, it looks up the parent comment’s paragraph ID so Word can connect the reply to the original comment. The script does not edit `document.xml` itself; instead, when run from the command line, it prints the exact marker XML that must be inserted around the text being annotated. One important quirk: if a reply names a missing parent, the main comment entry may already have been appended before the error is returned.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character hexadecimal label that Word uses as an internal paragraph or durable comment identifier. It is like printing a random ticket number for the new comment parts to share.

**Data flow**: It takes no input. It asks Python for a random number in Word’s expected range, formats that number as uppercase hexadecimal text padded to eight characters, and returns that string.

**Call relations**: `insert_comment` calls this when starting a new comment. The returned values are then passed into the XML-building helpers so the separate comment files can refer to the same comment consistently.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces curly quotation marks and apostrophes with XML character references. This keeps those characters written in a form that is safe and predictable inside Word’s XML files.

**Data flow**: It receives a text string. It scans for the four common curly quote characters, replaces each one with its matching XML entity text, and returns the changed string.

**Call relations**: `_serialize_xml` calls this after turning an XML tree into text. It is the final cleanup step before XML bytes are written back to disk by `_append_element_to_file`.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an editable XML tree. Other helpers use it whenever they need to inspect or add to an existing DOCX XML file.

**Data flow**: It receives a file path. It reads that file as bytes, gives the bytes to the XML parser from `lxml`, and returns the root XML element that represents the file’s contents in memory.

**Call relations**: `_append_element_to_file` uses it before adding a new child element. `_ensure_registrations` uses it to inspect package metadata. `_resolve_parent_paragraph` uses it to search existing comments for a parent reply target.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an in-memory XML tree back into bytes ready to save to a file. It also applies the curly-quote cleanup needed by this script’s Word XML output.

**Data flow**: It receives the root of an XML tree. It serializes that tree with an XML declaration and UTF-8 encoding, converts the bytes briefly to text so curly quotes can be replaced, then returns UTF-8 bytes again.

**Call relations**: `_append_element_to_file` calls this after it has added a new XML child. `_serialize_xml` delegates the quote replacement to `_escape_curly_quotes` before the file is written.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the actual comment text entry for `comments.xml`. This is the part Word reads to show the comment author, date, initials, and visible comment body.

**Data flow**: It receives the comment ID, author details, timestamp, paragraph ID, and body text. It creates a `w:comment` XML element with a paragraph, a standard comment-reference run, and a text run containing the comment text, then returns that element without saving it.

**Call relations**: `insert_comment` calls this after generating IDs and a timestamp. The returned element is handed to `_append_element_to_file`, which adds it to `comments.xml`.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra Word metadata that says whether a comment is done and, for replies, which parent paragraph it belongs to. This helps Word display threaded comments correctly.

**Data flow**: It receives the new comment’s paragraph ID and, optionally, the parent comment’s paragraph ID. It creates a `commentEx` XML element marked as not done, adds the parent link if one exists, and returns the element.

**Call relations**: `insert_comment` calls this after it has either skipped parent lookup for a normal comment or resolved the parent paragraph for a reply. The result is appended to `commentsExtended.xml`.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the ID mapping entry that connects a comment paragraph ID to a durable ID. Word uses this extra ID to keep tracking comments across edits.

**Data flow**: It receives a paragraph ID and a durable ID. It creates a `commentId` XML element containing both values and returns that element.

**Call relations**: `insert_comment` creates both IDs near the start of the operation, then calls this helper. The returned XML is appended to `commentsIds.xml` so Word can find the durable identity for the comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the newest-style Word metadata entry for a comment, including its durable ID and UTC date. This supports newer Word comment features.

**Data flow**: It receives a durable ID and timestamp. It creates a `commentExtensible` XML element with those attributes and returns it.

**Call relations**: `insert_comment` calls this after building the older comment metadata entries. The element is then appended to `commentsExtensible.xml` to complete the set of companion records Word expects.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the internal paragraph ID for an existing parent comment. Replies need this value so Word can attach the new comment underneath the correct older one.

**Data flow**: It receives the path to `comments.xml` and the numeric ID of the intended parent comment. It parses the file, searches comments for the matching ID, looks inside that comment for a paragraph with a Word paragraph ID, and returns that ID if found; otherwise it returns nothing.

**Call relations**: `insert_comment` calls this only when the new comment is a reply. It relies on `_parse_xml_file` to read the existing comments, and its result is passed into `_build_extended_element` to create the thread link.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to the end of an existing XML file and saves the file. It is the shared “open, add, write back” step for all comment companion files.

**Data flow**: It receives a file path and an XML child element. It parses the current file, appends the child to the root element, serializes the updated XML, and writes the new bytes back to the same path.

**Call relations**: `insert_comment` calls this repeatedly: once for the main comment and once for each companion metadata entry. It uses `_parse_xml_file` to load the file and `_serialize_xml` to prepare the updated file for saving.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure the DOCX package knows that the comment XML files exist. Without these relationship and content-type entries, Word may ignore the files even if they are present.

**Data flow**: It receives the base unpacked DOCX directory. It looks for `word/_rels/document.xml.rels` and `[Content_Types].xml`, checks whether comment parts are already registered, and if not, adds relationship records and content-type override records for all comment-related files before writing those metadata files back.

**Call relations**: `insert_comment` calls this when it detects that `comments.xml` did not already exist and template comment files have just been copied in. It uses `_parse_xml_file` to inspect existing metadata and `lxml` element creation to add the missing registrations.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds one normal comment or threaded reply to an unpacked DOCX folder. This is the main function other code or the command-line script uses when it wants Word’s comment side files updated.

**Data flow**: It receives the path to an unpacked DOCX folder and a `CommentSpec` containing the comment ID, text, author, initials, and optional parent ID. It checks for the `word` folder, creates IDs and a timestamp, copies template files if this is the first comment, registers those files, builds the needed XML elements, appends them to the correct files, and returns the new paragraph ID plus a success or error message. If the parent reply target is missing, it returns an error after the main comment entry has already been written.

**Call relations**: This is the center of the file. The command-line block builds a `CommentSpec` from user arguments and calls `insert_comment`; inside, it calls the ID maker, XML builders, registration helper, parent resolver, and append helper in sequence. After it returns, the command-line path prints either an error or marker instructions for editing `document.xml`.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`io_transport` · `manual document packaging`

A DOCX file is really a ZIP archive full of XML files and related resources. This script is the “put it back in the box” tool: it takes a folder that was previously unpacked from a DOCX, cleans up unnecessary XML whitespace, and writes everything into a new .docx archive.

The main function first checks two practical things: the input must be a real directory, and the output name must end in .docx. It then copies the whole input folder into a temporary staging area. That staging copy matters because the script edits XML files while packing, and it should not rewrite the user’s original working folder.

For every XML and relationship file in the staging copy, it removes whitespace-only text between XML elements. It carefully skips Word text elements, because spaces inside those elements can be meaningful document content. In everyday terms, it trims the packing material around the document structure, but does not erase words or spaces that belong on the page.

Finally, it creates the output folder if needed and writes the staged files into a compressed ZIP archive with a .docx extension. If XML cleanup fails, it reports the file name to standard error and stops rather than silently producing a questionable document.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing routine. It takes a folder containing the parts of a DOCX document, cleans the XML files in a safe temporary copy, and creates the final .docx file.

**Data flow**: It receives an input directory path and an output file path. It checks that the input is a directory and that the output looks like a DOCX file, copies the input into a temporary staging folder, asks `_strip_xml_whitespace` to clean each XML-style file there, then writes all staged files into a compressed DOCX archive. It returns either the path to the created file with a success message, or `None` with an error message.

**Call relations**: When the script is run from the command line, this is the function that does the real work after argument parsing. During packing, it calls `_strip_xml_whitespace` for each `.xml` and `.rels` file before handing the staged files to Python’s ZIP-writing library to create the final document.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing indentation and spacing that only exists between XML tags. It avoids changing Word text fields where whitespace may be part of the visible document.

**Data flow**: It receives the path to one XML-related file. It parses the file into an XML tree, walks through each element, removes whitespace-only text and tail content where it is safe, and removes unusual callable-tag children that do not belong in the saved XML. It then writes the cleaned XML back to the same file in UTF-8 format. If something goes wrong, it prints an error naming the file and raises the failure so packing stops.

**Call relations**: This function is called by `pack_docx` while preparing the temporary staging copy. It does the focused cleanup step, then returns control so `pack_docx` can continue cleaning the rest of the files and eventually zip the package.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).
