# Office PPTX Command-Line Utilities  `stage-10.3.10`

This stage provides small command-line tools for working with PowerPoint .pptx files outside the main application flow. A .pptx file is really a zip package full of XML files, images, and links; these tools let developers inspect, edit, rebuild, and fix that package safely. The empty __init__.py file simply makes the scripts folder importable as normal Python code. unpack.py opens a .pptx like a suitcase: it unzips the contents into a folder, formats the XML so humans can read it, and protects curly quotes in a form XML will not misread. pack.py does the reverse, turning that folder back into a usable .pptx while compacting the XML without changing slide text. repair.py fixes known problems made by pptxgenjs, a library that generates presentations, so PowerPoint is less likely to complain or alter spacing. slides.py is the workbench tool: it can remove unused parts, add a slide, or make thumbnail contact sheets for quick visual review.

## Files in this stage

### Script Package Setup
Package metadata that makes the PPTX command-line utilities importable as Python modules.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import/package discovery`

This file does not contain any executable code, but it still has a job. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as a package, meaning a named collection of modules that can be imported elsewhere. Here, it marks the `scripts` directory inside the Office PowerPoint (`pptx`) skill as importable Python code.

Think of it like putting a label on a drawer: the drawer may be empty at the front, but the label tells the system how to find and use what belongs inside. Without this file, some Python environments or tools might not recognize `scripts` as a package, especially if they rely on the older, explicit package style. That could make imports less predictable.

Because the file is empty, it does not set up state, define helpers, run startup logic, or change behavior directly. Its value is structural: it helps the project keep a clear module layout for PowerPoint-related document scripts.


### PPTX Archive Round Trip
Utilities for unpacking editable presentation contents and packing them back into a clean `.pptx` archive.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `on-demand document preparation before XML editing`

A .pptx file is really a ZIP archive full of XML files and related assets. This script opens that archive like a suitcase, lays its contents out in a normal folder, and tidies the XML so a person or another tool can edit it more safely. Without this kind of unpacking step, changing a presentation at the XML level would be awkward because the useful parts are hidden inside the zipped PowerPoint package.

The main flow checks that the input file exists and really has a .pptx extension. It then creates the destination folder, extracts the ZIP contents into it, and looks for XML files, including .rels relationship files that PowerPoint uses to connect slides, images, themes, and other parts. Each XML-like file is first pretty-printed, meaning it is rewritten with consistent indentation so the structure is easier to inspect. Then the script replaces curly “smart quotes” with explicit XML character entities, which are text codes such as &#x201C;. This helps keep those characters stable when XML is later edited or processed.

The script is forgiving: if one XML file cannot be parsed or rewritten, the helper silently skips that failure instead of stopping the whole unpack. The top-level command-line section prints either a success message or an error and exits with a failure code when something is wrong.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker that unpacks a PowerPoint file into a folder and prepares its XML files for editing. A caller uses it when they want a readable directory version of a .pptx package instead of one compressed file.

**Data flow**: It starts with two pieces of text: the path to the .pptx file and the path to the output folder. It checks that the source exists and looks like a PowerPoint file, creates the target folder, opens the .pptx as a ZIP archive, and extracts everything. It then finds all .xml and .rels files, sends each one through XML formatting, then sends each one through smart-quote escaping. It returns either an ExtractionResult containing the number of XML files processed plus a success message, or no result plus an error message.

**Call relations**: This function is the hub of the script. It is called by the command-line block when someone runs the file directly, and it calls _prettify_xml and _escape_smart_quotes in sequence for every XML-like file it finds. It also relies on the standard ZIP and path tools to open the PowerPoint package and write the extracted folder.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper rewrites one XML file with neat, consistent indentation. It exists so the unpacked PowerPoint internals are easier for humans and text-based tools to read.

**Data flow**: It receives a file path. It tries to parse that file as XML, asks the XML library to add two-space indentation, then writes the formatted XML bytes back to the same file with a UTF-8 XML declaration. If parsing or writing fails, it leaves the file alone and does not raise an error.

**Call relations**: extract_pptx calls this once for each .xml and .rels file after extraction. After _prettify_xml has made the file easier to read, extract_pptx later passes the same file to _escape_smart_quotes for character cleanup.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks with XML character codes. It is used to make those special quote characters explicit and stable inside the unpacked XML text.

**Data flow**: It receives a file path, reads the file as UTF-8 text, and checks whether it contains curly single or double quotation marks. If none are found, it changes nothing. If they are found, it replaces each one with its matching XML entity and writes the updated text back to the same file. If reading or writing fails, it quietly leaves the file unchanged.

**Call relations**: extract_pptx calls this after _prettify_xml for every XML-like file. In the larger flow, this is the final cleanup step before extract_pptx reports how many XML files were prepared.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`io_transport` · `command execution`

A .pptx file is really a ZIP archive with many XML files inside it. This script is the “put it back in the box” tool: it takes a folder that contains those unpacked pieces, cleans up unnecessary whitespace in the XML, and zips everything into a new PowerPoint file.

The main job is done by assemble_pptx. It first checks that the input is a real directory and that the output name ends in .pptx. Then it copies the whole folder into a temporary work area. This matters because the script rewrites XML files, and using a temporary copy prevents it from changing the original unpacked folder.

Next it finds every .xml and .rels file. A .rels file is an XML relationship file used by Office documents to connect slides, images, layouts, and other parts. Each one is passed through _condense_xml, which removes formatting-only spaces and line breaks. It is careful not to strip actual text inside DrawingML text elements, where slide words live.

Finally, it creates the output folder if needed and writes every file from the temporary work area into a compressed ZIP archive with a .pptx name. If XML cleanup fails, the script prints a clear error and stops rather than silently producing a broken presentation.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: Builds a .pptx file from a directory containing unpacked PowerPoint contents. It protects the original files by working on a temporary copy, cleans XML-like files, and then compresses the result into the final presentation file.

**Data flow**: It receives a source directory path and an output file path. It checks that the source is a directory and the destination ends in .pptx; if either check fails, it returns no file path and an error message. Otherwise it copies the source into a temporary folder, asks _condense_xml to clean each .xml and .rels file, creates the output directory if needed, writes all files into a ZIP archive, and returns the destination path with a success message.

**Call relations**: This is the main worker used by the command-line block at the bottom of the file. During its run, it calls _condense_xml for each XML-related file before handing the cleaned temporary tree to Python’s ZIP-writing tools to make the final .pptx archive.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: Cleans one XML file by removing whitespace that only exists for formatting, while preserving real text that appears on slides. This helps make the packed presentation tidier without changing visible content.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through each node, removes blank-only text and blank-only tail spacing except in protected text elements, removes unusual callable-tag child nodes, and writes the cleaned XML back to the same file using UTF-8 encoding. If anything goes wrong, it prints an error naming the file and raises the failure so packing stops.

**Call relations**: assemble_pptx calls this function while preparing the temporary copy of the presentation. _condense_xml does the careful XML cleanup step, then returns control so assemble_pptx can continue collecting files into the final compressed .pptx.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### Presentation Repair and Editing
Command-line tools for fixing known generated-PPTX issues, cleaning presentation parts, adding slides, and producing thumbnail contact sheets.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`entrypoint` · `post-generation repair or command-line use`

A .pptx file is really a ZIP package full of XML files. This script opens that package, checks for a few known bad patterns, and rewrites the package without them. Without this cleanup, PowerPoint may complain that the presentation is broken, or worse, it may open the file but remove important leading or trailing spaces from text, such as indentation in code examples.

The script fixes three things. First, it removes references to slide master files that are listed in the package index but do not actually exist. That is like a table of contents pointing to a missing chapter. Second, it removes folder entries from the ZIP file, because the PowerPoint packaging rules expect files, not separate directory records. Third, it scans slide-related XML files for text runs and adds xml:space="preserve" when text begins or ends with a space or tab. That tells PowerPoint, "these spaces are intentional; do not trim them."

The main repair function reads the presentation, decides whether anything is wrong, writes a temporary corrected copy, and then replaces the original file only after the new one has been successfully created.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects intentional spaces in PowerPoint text. It finds text elements in slide-related XML files that start or end with a space or tab, then marks them so PowerPoint will keep that whitespace instead of trimming it away.

**Data flow**: It receives a dictionary whose keys are file names inside the .pptx package and whose values are the raw file bytes. It looks only at slide, layout, master, and notes XML files, parses each one as XML, changes matching text elements by adding xml:space="preserve", and returns a dictionary of only the changed files plus a count of how many text elements were fixed. Files that are not relevant, or XML files that cannot be parsed, are left alone.

**Call relations**: The main repair flow calls this after reading the contents of the .pptx ZIP package. Internally it uses lxml.etree.fromstring to turn XML bytes into an editable tree, and lxml.etree.tostring to turn changed XML back into bytes. Its results tell repair which files need to be replaced in the rebuilt package.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a .pptx file. Given a filename, it checks for known package and XML problems, rewrites the presentation if needed, and reports whether repairs were applied.

**Data flow**: It takes a path to a .pptx file. It first checks that the file exists, then opens it as a ZIP package and reads its non-folder entries. It records which slide master files really exist, removes any fake slide master references from [Content_Types].xml, asks _repair_whitespace_preservation to fix risky text spacing, and checks for directory entries. If nothing is wrong, it prints that no repair is needed. If repairs are needed, it writes a temporary ZIP file with corrected contents, removes bad directory entries, replaces changed XML files, moves the temporary file over the original, prints the number of fixes, and returns True. If the input file is missing, it prints an error and returns False.

**Call relations**: This function is called by the command-line block when someone runs the script with a .pptx filename. It coordinates the whole job: pathlib.Path is used to work with the file path, zipfile.ZipFile reads and writes the presentation package, regular expression helpers find and remove bad XML references, _repair_whitespace_preservation supplies text-spacing fixes, and shutil.move replaces the old presentation with the repaired copy.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command execution`

A `.pptx` file is really a zip file full of XML files and media files. Those files point to each other through relationship files, a bit like a set of labels saying which parts belong together. This script helps when editing that package by hand or with automation, where it is easy to leave broken or unused pieces behind.

The `clean` command looks at an unpacked PowerPoint folder, finds which slides are actually listed in the presentation, removes slide files that are no longer used, deletes leftover resource files such as images or charts that no relationship points to, and removes stale entries from `[Content_Types].xml`, the package index that tells PowerPoint what each file is.

The `add` command either copies an existing slide or creates a new blank slide connected to a chosen layout. It also registers the new slide in the package metadata and prints the XML line the user still needs to add to the slide list.

The `thumbnail` command works on a normal `.pptx` file. It asks LibreOffice to render the presentation to PDF, turns the PDF pages into JPEG images, inserts gray placeholders for hidden slides, and lays the results out in one or more labeled grid images. Without a tool like this, automated PowerPoint editing would be harder to inspect and more likely to leave files that confuse PowerPoint.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so the rest of the script can inspect or edit it. XML is the text format PowerPoint uses inside `.pptx` packages to describe slides, relationships, and package contents.

**Data flow**: It receives a file path. It opens and parses that XML file using the XML library, then returns the root element, which other functions can search or change.

**Call relations**: This is the shared XML reader for the script. Cleaning, adding slides, and editing relationship files all call it before they examine or modify PowerPoint package files.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to disk with an XML declaration and UTF-8 text encoding. It is used after the script has removed or added XML entries.

**Data flow**: It receives an XML root element and a destination path. It converts the XML tree into bytes and replaces the file at that path with the new XML content.

**Call relations**: Functions that alter PowerPoint metadata call this after making their changes. It is the counterpart to `_parse_xml`: one reads the package file, the other saves the updated version.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file that is currently pointed to by a PowerPoint relationship file. This helps decide which resources are still in use and which are safe to delete.

**Data flow**: It receives the unpacked PowerPoint folder. It scans every `.rels` relationship file, reads each relationship target, turns it into a path relative to the package root when possible, and returns a set of referenced paths.

**Call relations**: During cleaning, `run_clean` calls this before deleting resources. Its result is handed to `_remove_unreferenced_resources`, which uses it as the evidence for what must be kept.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds the slide XML files that are actually part of the presentation's slide order. A slide file may exist in the folder but not be shown in the deck, and this function separates the real slides from leftovers.

**Data flow**: It reads `ppt/presentation.xml` and `ppt/_rels/presentation.xml.rels`. It matches slide relationship IDs to slide file names, then checks which IDs appear in the presentation's slide list, returning the active slide names.

**Call relations**: This starts the slide-cleaning part of `run_clean`. Its active-slide set is passed to `_remove_orphan_slides`, which removes slide files not present in that set.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special `[trash]` folder inside the unpacked package. This gives the cleaning command a simple way to remove files that were deliberately set aside for deletion.

**Data flow**: It receives the unpacked folder path. If a `[trash]` directory exists, it deletes the plain files inside it, removes the directory, and returns the relative names of what it deleted.

**Call relations**: `run_clean` calls this after removing unused slides. The deleted file names are collected with other removals so stale package entries can be cleaned later.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that are present on disk but are not listed as active slides in the presentation. It also removes their companion relationship files and cleans matching presentation relationships.

**Data flow**: It receives the unpacked folder and the set of active slide file names. It scans `ppt/slides`, deletes slide XML files not in that set, deletes matching `.rels` files, updates `presentation.xml.rels` if needed, and returns the deleted paths.

**Call relations**: `run_clean` calls this soon after finding active slides. It uses `_parse_xml` and `_write_xml` when it needs to remove no-longer-valid slide relationships from the presentation relationship file.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused support files such as images, charts, drawings, themes, and notes. These files can be left behind when slides are removed or edited, and keeping them makes the package larger and messier.

**Data flow**: It receives the unpacked folder and a set of paths known to be referenced. It checks known PowerPoint resource folders and removes files not in that referenced set; it also removes relationship files whose parent resource file is gone. It returns the paths it deleted.

**Call relations**: `run_clean` calls this repeatedly. Because deleting one file can make another relationship file obsolete, the cleaner keeps recollecting references and calling this until there is nothing more to remove.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes package index entries for files that were deleted. `[Content_Types].xml` tells PowerPoint what kind of part each file is, so it should not mention files that no longer exist.

**Data flow**: It receives the unpacked folder and the list of removed file paths. It opens `[Content_Types].xml`, removes matching `Override` entries, and writes the XML back only if something changed.

**Call relations**: `run_clean` calls this at the end, after all deletion is finished. It relies on the accumulated deletion list from the earlier cleanup steps.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Performs the full cleanup process for an unpacked PowerPoint folder. It is the main worker behind the `clean` command.

**Data flow**: It receives the unpacked package path. It finds active slides, removes unused slides, clears the trash folder, repeatedly removes unreferenced resources, cleans stale content-type records, and returns a list of everything deleted.

**Call relations**: _cmd_clean calls this after checking that the folder exists. Inside, it coordinates the smaller cleanup helpers in the order needed to avoid leaving dangling package references.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide file number, such as making `slide8.xml` after the highest existing slide is `slide7.xml`. This avoids overwriting existing slide files.

**Data flow**: It receives the slides directory. It scans files named like `slideN.xml`, extracts their numbers, and returns one more than the largest number, or `1` if there are no slides.

**Call relations**: Both `_create_from_layout` and `_clone_existing` call this when they need a safe file name for a new slide.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to `[Content_Types].xml`, the package file that tells PowerPoint how to interpret each part. Without this registration, PowerPoint may not recognize the new slide correctly.

**Data flow**: It receives the unpacked folder and a slide file name. It opens the content-types XML, checks whether that slide already has an entry, adds one if missing, and writes the file back.

**Call relations**: After a slide file is created or copied, `_create_from_layout` and `_clone_existing` call this to make the package index aware of the new slide.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the presentation to the new slide. In a `.pptx`, the presentation does not point to slide files directly; it uses relationship IDs like labels on wires.

**Data flow**: It receives the unpacked folder and the new slide file name. It reads `presentation.xml.rels`, finds the largest existing `rId` number, returns the existing ID if the slide is already registered, or creates a new relationship and returns its new ID.

**Call relations**: _create_from_layout and `_clone_existing` call this after adding the slide file. The returned relationship ID is printed for the user so they can add the matching slide-list entry in `presentation.xml`.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Finds the next numeric slide ID for the presentation's slide list. PowerPoint uses these IDs inside `presentation.xml` in addition to relationship IDs.

**Data flow**: It receives the unpacked folder. It reads `ppt/presentation.xml`, extracts existing slide IDs, and returns one more than the largest, or `256` if none are found.

**Call relations**: _create_from_layout and `_clone_existing` use this when printing the slide-list XML that the user should add for the new slide.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide connected to an existing slide layout. A layout is the template-like structure that defines placeholders and design rules for a slide.

**Data flow**: It receives the unpacked folder and a layout file name. It verifies the layout exists, creates a new blank slide XML file, creates a relationship file pointing to the layout, registers the slide in package metadata, and prints the XML entry needed to place it in the deck.

**Call relations**: run_add calls this when the requested source looks like a slide layout file. It uses the numbering and registration helpers to add the slide without colliding with existing files.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Copies an existing slide to create a new slide file. It deliberately removes copied notes-slide relationships so the duplicate does not point to the original slide's speaker notes.

**Data flow**: It receives the unpacked folder and source slide file name. It checks that the source exists, copies the slide XML and its relationship file if present, removes notes relationships from the copied relationships, registers the new slide, and prints the XML entry needed to include it in the presentation.

**Call relations**: run_add calls this when the source is not a layout file. It relies on `_parse_xml` and `_write_xml` only if there is a copied relationship file to clean.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses the right way to add a slide: either create a blank slide from a layout or duplicate an existing slide. It is the main worker behind the `add` command.

**Data flow**: It receives the unpacked folder and a source name. If the source name looks like `slideLayout...xml`, it creates from that layout; otherwise it clones the named slide. It does not return a value, but it creates files and prints instructions.

**Call relations**: _cmd_add calls this after validating the unpacked folder. It dispatches to `_create_from_layout` or `_clone_existing` based on the source name.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a `.pptx` file to discover the presentation's slide order and which slides are hidden. This matters because rendered pages may omit hidden slides, but the thumbnail grid should still show where they belong.

**Data flow**: It receives the `.pptx` file path. It opens the zip package, reads the presentation relationship file and presentation XML, matches relationship IDs to slide names, and returns an ordered list of slide records with hidden flags.

**Call relations**: run_thumbnail calls this before rendering images. Its ordered list later guides `_pair_slides_with_images`, so thumbnails match the actual deck order.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the visible slides in a `.pptx` into JPEG image files. It uses external programs: LibreOffice (`soffice`) to make a PDF, then `pdftoppm` to convert PDF pages to images.

**Data flow**: It receives the presentation path and a temporary working folder. It writes a PDF into that folder, converts the PDF pages to JPEG files, and returns the sorted image paths. If either conversion fails, it raises an error.

**Call relations**: run_thumbnail calls this after learning the slide order. The image paths it returns are paired with slide names and hidden-slide placeholders before the grid is built.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a gray crossed-out image to stand in for a hidden slide. This keeps hidden slides visible in the thumbnail overview even though they may not be rendered as normal pages.

**Data flow**: It receives image dimensions. It creates a new gray image of that size, draws two diagonal lines across it, and returns the image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden. The placeholder is saved as a temporary JPEG and included in the thumbnail grid.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (Draw, new).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches the ordered slide list to the rendered image files and inserts placeholders for hidden slides. It acts like aligning a printed stack of visible pages with the full table of contents.

**Data flow**: It receives slide order records, rendered image paths, and a temporary folder. It determines placeholder size, walks through the slide order, pairs visible slides with the next rendered image, creates placeholder images for hidden slides, and returns pairs of image path plus label.

**Call relations**: run_thumbnail calls this after rendering. It uses `_make_hidden_placeholder` for hidden slides, then hands the completed image-and-label list to `_compose_grid`.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one contact-sheet image from slide thumbnails. Each cell contains a centered label and a scaled-down slide image with a thin outline.

**Data flow**: It receives image-and-label pairs, a column count, and a cell width. It calculates the needed canvas size, creates a white background, draws each label, resizes each slide image to fit, pastes it into place, draws outlines, and returns the finished image.

**Call relations**: run_thumbnail calls this once for each chunk of slides that fits in a grid. The returned image is then saved as a JPEG output file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (Draw, load_default, new, open).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG thumbnail grids for a PowerPoint presentation. It is the main worker behind the `thumbnail` command.

**Data flow**: It receives a `.pptx` path, an output prefix, and a column count. It reads slide order, renders slide images in a temporary folder, pairs images with slide labels, splits them into grid-sized chunks, saves each grid as a JPEG, and returns the saved file paths.

**Call relations**: _cmd_thumbnail calls this after checking the input file and limiting the column count. It coordinates the thumbnail pipeline from package reading, to rendering, to grid composition.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `clean`. It checks the user's input, runs the cleanup, and prints a human-readable report.

**Data flow**: It receives parsed command-line arguments. It turns the folder argument into a path, exits with an error if it does not exist, calls `run_clean`, then prints either the removed files or a message that nothing was found.

**Call relations**: The argument parser connects the `clean` subcommand to this function. It is the command-line wrapper around the deeper cleanup logic.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `add`. It validates the unpacked PowerPoint folder and then asks the add-slide logic to do the work.

**Data flow**: It receives parsed command-line arguments. It turns the folder argument into a path, exits with an error if missing, then passes the path and source name to `run_add`.

**Call relations**: The parser connects the `add` subcommand to this function. It keeps user-facing validation separate from the slide creation and cloning details.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `thumbnail`. It validates the `.pptx` input, caps the grid column count, runs thumbnail creation, and prints the output file names.

**Data flow**: It receives parsed command-line arguments. It checks that the input exists and has a `.pptx` extension, limits columns to the allowed maximum, calls `run_thumbnail`, and reports success or exits with an error message if thumbnail creation fails.

**Call relations**: The parser connects the `thumbnail` subcommand to this function. It is the user-facing shell around the rendering and grid-building pipeline.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Builds the command-line interface for the script. It defines the available subcommands, their arguments, help text, defaults, and which function should run for each command.

**Data flow**: It takes no input. It creates an argument parser, adds `clean`, `add`, and `thumbnail` subcommands with their expected arguments, attaches each subcommand to its command function, and returns the parser.

**Call relations**: When the script is run directly, the bottom of the file calls this function, parses the user's command, and then calls the selected command function.

*Call graph*: 1 external calls (ArgumentParser).
