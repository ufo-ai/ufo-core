# Word DOCX package and comment helpers  `stage-10.2.2`

This stage is a set of command-line tools for working on Microsoft Word DOCX files behind the scenes. A DOCX file is really a zipped package of XML files, where XML is structured text that describes the document. These helpers let an automated workflow open that package, change it safely, and build it again.

The flow starts with unpack.py, which unzips the DOCX into a folder and cleans up the main document XML so it is easier for tools or people to read and edit. If the document has tracked changes, accept_changes.py can first make a clean version by asking LibreOffice to accept all changes without showing any window, which is useful on servers. Once the package is unpacked, comment.py can insert a new Word comment or a reply by writing the special XML entries and links Word needs. Finally, pack.py zips the folder back into a valid DOCX and trims unnecessary XML spacing. Together, they act like a careful unpack-edit-repack workbench for Word documents.

## Files in this stage

### Tracked-change cleanup
Prepare a clean DOCX by accepting revisions before package-level editing begins.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`io_transport` · `document processing`

Tracked changes in a Word document are like sticky notes showing edits that have not yet been formally accepted. This file turns such a document into a final version where all those edits are accepted. It first copies the input DOCX to the requested output path, so the original file is not changed. Then it prepares a small LibreOffice Basic macro, which is a tiny script LibreOffice can run inside the document. That macro tells LibreOffice to run its built-in “Accept All Tracked Changes” command, save the document, and close it.

The script uses a separate temporary LibreOffice user profile under `/tmp`. That matters because LibreOffice macros must live inside a profile before they can be run. The helper code creates the profile if needed and writes the macro into the right place.

A surprising detail is that LibreOffice may hang even after successfully saving the document. Because of that, the script treats a timeout during the final LibreOffice run as success. This is intentional: by then the macro has usually already accepted the changes and saved the file. Without this file, another part of the system would need to manually open DOCX files or reimplement document revision handling, which is difficult and unreliable.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This function builds the environment settings used when starting LibreOffice. It forces LibreOffice to use a simple headless-friendly display backend, so it can run without a normal graphical desktop.

**Data flow**: It starts with a copy of the current process environment, adds or replaces the `SAL_USE_VCLPLUGIN` setting with `svp`, and returns that modified environment dictionary. Nothing on disk is changed.

**Call relations**: When `_ensure_macro` or `accept_tracked_changes` starts LibreOffice through `subprocess.run`, they call `_soffice_env` first so LibreOffice receives the right environment settings for background execution.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This function creates the command-line argument that tells LibreOffice to use this script’s temporary user profile. A LibreOffice profile is like a personal settings folder, and here it is also where the macro is installed.

**Data flow**: It reads the fixed `PROFILE_DIR` path and turns it into the exact `-env:UserInstallation=...` string LibreOffice expects. It returns that string without changing files or launching anything.

**Call relations**: Both `_ensure_macro` and `accept_tracked_changes` use this helper when building LibreOffice commands, so the profile used to install the macro is the same profile used to run it.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This function makes sure LibreOffice has the Basic macro needed to accept tracked changes. It creates or initializes the temporary LibreOffice profile if necessary, then writes the macro file into the correct folder.

**Data flow**: It checks whether the macro file already exists and contains the expected macro name. If so, it reports success immediately. If the macro folder does not exist, it briefly starts LibreOffice in headless mode to initialize the profile, then creates the macro directory. Finally, it writes the macro XML text to the module file and returns `True`.

**Call relations**: `accept_tracked_changes` calls this before trying to process the DOCX file. Inside, `_ensure_macro` asks `_profile_arg` for the profile command-line option and `_soffice_env` for the safe headless environment, then uses `subprocess.run` to let LibreOffice create its profile structure when needed.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main work function. Given an input DOCX and an output path, it copies the document, runs LibreOffice on the copy, accepts all tracked changes, and returns a human-readable success or error message.

**Data flow**: It receives two file paths as strings. It turns them into `Path` objects, checks that the input exists and has a `.docx` extension, creates the output folder if needed, and copies the input file to the output location. It then ensures the LibreOffice macro is installed. After that, it runs LibreOffice in headless mode with the macro URI and the output file path. The function returns a tuple whose first value is always `None` and whose second value is a message saying either what went wrong or that the tracked changes were accepted.

**Call relations**: This function is the script’s central path and is also what the command-line block calls after parsing arguments. It depends on `_ensure_macro` to prepare LibreOffice, uses `_profile_arg` and `_soffice_env` to build the LibreOffice command correctly, uses `shutil.copy2` to preserve the original document, and uses `subprocess.run` to launch LibreOffice. If LibreOffice times out during the final run, it still returns a success message because LibreOffice often hangs after the document has already been saved.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### DOCX package editing
Unpack the DOCX, add comment or reply XML into the package, and repack it into a finished document.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual command-line document unpacking`

A .docx file is really a ZIP archive full of XML files. This file is a small command-line tool that opens that archive, extracts its contents, and makes the XML easier to inspect and change. Without this step, anyone trying to work on the raw Word document structure would face cramped XML, scattered text runs, and noisy tracked-change markup.

The main flow is like unpacking a suitcase and then sorting the clothes. First, the script checks that the input exists and has a .docx extension. It creates the output folder and unzips the document into it. Then it finds XML-like files, including relationship files ending in .rels, and pretty-prints them with indentation.

For the main Word content file, word/document.xml, it can do two deeper cleanups. One cleanup coalesces tracked changes, meaning it joins neighboring insertions or deletions made by the same author when they are really one continuous change. The other cleanup merges adjacent Word “runs,” which are small pieces of text with the same formatting. Word often splits text into many tiny runs, so merging them makes editing less frustrating.

Finally, it replaces curly quote characters with XML numeric entities. This keeps those characters explicit in the file text. The script returns a short summary and can also be run directly from the command line.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker for the script. It validates the input Word file, unzips it, formats the XML, optionally simplifies the main document XML, and returns both a structured result and a human-readable message.

**Data flow**: It receives an input file path, an output folder path, and two true-or-false options for cleanup steps. It checks the file, extracts the DOCX archive into the folder, finds XML and relationship files, indents them, optionally rewrites word/document.xml, replaces curly quotes, and then returns an UnpackResult plus a summary string. If the file is missing, not a .docx, or not a valid ZIP archive, it returns no result and an error message instead.

**Call relations**: This function is the hub of the file. The command-line block calls it after reading arguments from the user. During the unpacking flow, it hands individual XML files to _indent_xml and _replace_curly_quotes, and it sends word/document.xml to _coalesce_tracked_changes and _merge_adjacent_runs when those options are enabled.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This function makes one XML file easier for humans to read by adding consistent indentation. It is a cleanup step after the DOCX archive has been extracted.

**Data flow**: It receives a path to an XML file. It tries to parse the file as XML, asks lxml to indent the tree, and writes the pretty-printed XML back to the same file. If parsing or writing fails, it quietly leaves the file unchanged.

**Call relations**: unpack_docx calls this for every extracted .xml and .rels file before deeper document-specific cleanup happens. It does not call back into the rest of the script; it is a focused formatting helper.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This function rewrites curly quotation marks as explicit XML numeric entities. That makes those special characters visible and stable in the text form of the XML.

**Data flow**: It receives a path to a text file, reads it as UTF-8, searches for curly single or double quotes, and replaces each one with its matching entity such as &#x201C;. If there are no curly quotes, it does nothing. If reading or writing fails, it quietly leaves the file as it was.

**Call relations**: unpack_docx calls this near the end for every extracted XML-style file. It runs after indentation and document.xml cleanup, so it is the final text-level polish step.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word’s main document XML by joining neighboring text runs that have the same formatting. This reduces the clutter Word often creates when it splits normal text into many tiny pieces.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns zero. Otherwise it parses the XML, removes proofing-error markers, removes run attributes related to Word revision IDs, finds parent elements that contain runs, and asks _merge_runs_in to merge compatible runs inside each parent. If anything was merged, it writes the updated XML back and returns the number of absorbed runs.

**Call relations**: unpack_docx calls this when the merge-runs option is enabled. This function coordinates the run-merging pass and delegates the actual per-container merging to _merge_runs_in.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This function creates a normalized signature for a run’s formatting. The script uses that signature to decide whether two neighboring runs are formatted the same way and can safely be merged.

**Data flow**: It receives a Word run XML element. It looks for the run-properties child, which describes formatting such as style or emphasis. If there is no formatting child, it returns None; otherwise it converts that formatting XML into a canonical, stable string and returns it.

**Call relations**: _merge_runs_in calls this while scanning neighboring runs. Its returned signature is the comparison key that lets _merge_runs_in group only runs with matching formatting.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This function merges compatible runs within one parent XML element. It keeps the first run as the anchor and moves the later runs’ content into it when they are adjacent and formatted the same.

**Data flow**: It receives a container element, such as a paragraph-like XML element. It walks through the container’s direct children, groups consecutive Word run elements that share the same formatting signature, and skips across non-run elements because those break a merge group. For each mergeable group, it moves non-formatting children from later runs into the first run, removes the emptied later runs, joins adjacent text nodes inside the anchor, and returns how many runs were absorbed.

**Call relations**: _merge_adjacent_runs calls this once for each container that owns runs. It relies on _canonical_rpr to compare formatting and calls _join_adjacent_text after moving content so the merged run does not still contain unnecessary neighboring text nodes.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This function tidies a single run after other runs have been merged into it. If two text nodes sit next to each other, it combines them into one text node.

**Data flow**: It receives one Word run XML element. It scans the run’s children from left to right, and whenever it finds two neighboring text elements, it joins their text into the first one and removes the second. If the merged text starts or ends with a space, it marks the text so XML readers preserve that space.

**Call relations**: _merge_runs_in calls this after it has moved content from donor runs into an anchor run. It is the final cleanup that makes the merged run simpler and protects meaningful spaces.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This function simplifies tracked-change markup in word/document.xml. It joins neighboring insertions or deletions from the same author when they are part of one continuous change.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns zero. Otherwise it reads and parses the XML while preserving blank text, finds paragraph and table-cell containers, and asks _coalesce_in to process insertion and deletion elements inside each one. If any change elements were merged, it writes the updated XML back and returns the number of removed change wrappers.

**Call relations**: unpack_docx calls this when the coalesce-changes option is enabled. It is the top-level tracked-change cleanup pass and delegates the detailed same-author merging to _coalesce_in.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This function looks inside one container for tracked insertions or deletions of a single kind and groups them by author. It prepares those groups so adjacent changes by the same person can be joined.

**Data flow**: It receives a container XML element and a change type, either insertion or deletion. It collects direct child elements of that type, groups them by the Word author attribute, sends each author group to _merge_change_run, and returns the total number of elements merged away.

**Call relations**: _coalesce_tracked_changes calls this for each paragraph and table cell, once for insertions and once for deletions. It hands each same-author sequence to _merge_change_run, which performs the actual XML rewriting.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This function merges a sequence of tracked-change elements when they are truly adjacent. It keeps the first element as the anchor and moves later change contents into it when no meaningful content separates them.

**Data flow**: It receives a list of insertion or deletion XML elements that already share the same author grouping. It walks through them in order, checks whether the current anchor and the next element are adjacent using _changes_adjacent, moves the later element’s children into the anchor when they are, preserves any tail text in the surrounding parent, removes the later wrapper, and counts how many wrappers were absorbed. If a later element is not adjacent, it becomes the new anchor.

**Call relations**: _coalesce_in calls this for each grouped run of same-author changes. It depends on _changes_adjacent to avoid merging changes that only look related but are separated by real content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This function answers a safety question: are two tracked-change elements next to each other except for whitespace or comments? The script uses it to avoid incorrectly merging changes across real document content.

**Data flow**: It receives two XML elements. It finds their shared parent, locates both elements among that parent’s children, inspects anything between them, and allows only comments plus whitespace. It returns true if there is no meaningful text or element between them, and false otherwise.

**Call relations**: _merge_change_run calls this before merging one tracked-change element into another. Its yes-or-no result protects the coalescing step from joining separate edits that should remain separate.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `manual DOCX comment insertion run`

A DOCX file is really a folder of XML files packaged as a zip. Adding a comment is not as simple as writing text in one place: Word expects several companion files, relationship records, content type records, and later marker tags inside document.xml. This file automates the boilerplate part, like filling out all the background paperwork before the visible comment marker is placed in the document body.

The main input is an unpacked DOCX directory, a unique comment ID, the comment text, and optional author or parent-comment information. If this is the first comment in the document, the script copies template comment files into the word folder and registers them in the DOCX package files. It then creates four linked XML entries: the visible comment body, modern Word metadata for threading, a durable ID, and an extensible metadata record with a timestamp.

For replies, it looks up the parent comment’s paragraph identifier so Word can connect the reply to the right thread. One important caveat: the new comment body is appended before the parent is checked, so a missing parent can leave a partial change in comments.xml. After success, the command-line mode prints instructions for the user to insert the required range markers into document.xml manually.

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

`entrypoint` · `manual document packaging`

A DOCX file is really a ZIP archive full of XML files and related resources. This script is the “put it back in the box” tool: it takes a folder that represents an opened-up DOCX document, cleans up unnecessary XML spacing, and zips everything into a `.docx` file again. Without this step, edits made to the unpacked folder would not become a Word document that normal tools can open.

The main flow starts by checking two practical things: the input must be a directory, and the output must end in `.docx`. It then copies the whole folder into a temporary staging area, like making a safe workbench copy before packing a suitcase. In that staging copy, it finds XML files and relationship files (`.rels`, which describe how DOCX parts connect to each other) and removes whitespace that only exists for formatting the XML source. It carefully avoids stripping whitespace inside actual text tags, because spaces in document text can be meaningful.

Finally, it creates the destination folder if needed and writes every staged file into a compressed ZIP archive with the requested `.docx` name. If run directly from the command line, it accepts an input folder and output path, prints the result message, and exits with an error code if the request was invalid.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing function. It validates the requested input and output, cleans the document XML in a temporary copy, and creates the final `.docx` ZIP archive.

**Data flow**: It takes an input directory path and an output file path. First it turns them into filesystem path objects and checks that the source is a folder and the destination looks like a DOCX file. If either check fails, it returns no output path and an error message. Otherwise, it copies the source folder into a temporary staging folder, asks `_strip_xml_whitespace` to clean each XML and relationship file, writes all staged files into a compressed `.docx` archive, and returns the destination path plus a success message.

**Call relations**: When the script is used from the command line, this is the function the command-line wrapper calls after reading the two arguments. During its work, it delegates XML cleanup to `_strip_xml_whitespace`, uses a temporary directory so the original unpacked document is not changed, copies files with `shutil.copytree`, and writes the final archive through `zipfile.ZipFile`.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper removes XML-only whitespace from one XML-like file while preserving spaces that are part of visible document text. It keeps the DOCX package cleaner without accidentally changing what the user wrote.

**Data flow**: It receives the path to one XML or `.rels` file. It parses that file into an XML tree, walks through every element, and skips special text elements where whitespace may matter. For other elements, it removes text or tail spacing that is only blank formatting, and it removes unusual callable-tag child nodes such as processing instructions or comments. It then writes the cleaned XML back to the same file with a UTF-8 XML declaration. If parsing or writing fails, it prints a clear error to standard error and raises the original problem again.

**Call relations**: This function is called by `pack_docx` for each XML and relationship file in the temporary staging copy. It relies on `lxml.etree.parse` to read the XML structure and `lxml.etree.tostring` plus `Path.write_bytes` to save the cleaned version before `pack_docx` zips the staged folder into the final DOCX.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).
