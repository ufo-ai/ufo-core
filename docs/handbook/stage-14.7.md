# PowerPoint PPTX package, repair, and slide tools  `stage-14.7`

This stage is a set of behind-the-scenes tools for working with PowerPoint .pptx files. A .pptx is really a zipped package of many smaller files, including XML files, which are text files that describe slides, shapes, text, and links. These scripts let the system open that package up, adjust it, fix it, and close it again.

The empty __init__.py file simply tells Python that this scripts folder can contain importable tools. unpack.py is the “open the box” step: it expands a .pptx into a normal folder and formats the XML so it is easier to inspect or edit. pack.py is the matching “close the box” step: it rebuilds the folder into a .pptx and removes extra XML spacing without changing slide text. repair.py fixes known problems in generated presentations, especially ones made by pptxgenjs, so PowerPoint accepts them cleanly. slides.py is a small toolbox for practical slide work: removing unused package files, adding slides, and making thumbnail contact sheets for quick visual review.

## Files in this stage

### Script package setup
Package metadata that makes the PowerPoint script directory importable.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import/package discovery`

This file contains no code, but it still has a job. In Python projects, an `__init__.py` file is commonly used as a signpost that says, “this folder belongs to a Python package.” Think of it like a blank label on a drawer: the label does not store anything itself, but it helps the rest of the system understand how the drawer fits into the cabinet.

Here, the drawer is the `scripts` directory inside the Office PowerPoint extension area. Other files in or near this folder may contain the real behavior for working with PowerPoint documents. This file simply makes the folder easier and safer to treat as part of the Python module structure.

Because it is empty, it does not run setup code, define shared variables, or change behavior when imported. That is important: importing this package has no side effects. If this file were removed, some import paths or packaging tools might still work in modern Python, but older tools or stricter project assumptions could fail to recognize the folder as intended.


### PPTX unpacking and repacking
Tools for converting between editable unpacked presentation folders and finalized PowerPoint files.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual preprocessing / command-line run`

A .pptx file is really a ZIP archive: a bundled folder of XML files and other resources. This file is a small command-line tool that opens that bundle, extracts it into a normal directory, and then cleans up the XML files it finds. Without a helper like this, editing PowerPoint internals would be awkward because the useful content is hidden inside a compressed package and often written as dense, hard-to-read XML.

The main flow is simple. First it checks that the input file exists and has the .pptx ending. Then it creates the destination folder if needed and unzips the PowerPoint file into it. After that, it searches the extracted tree for XML files, including .rels relationship files, which describe how PowerPoint parts link to each other. Each XML file is parsed and rewritten with consistent indentation, like turning a cramped paragraph into a neatly outlined document. Finally, it replaces curly “smart quotes” with explicit XML character entities so those characters are preserved in a predictable XML-safe form.

The cleanup helpers are deliberately forgiving: if one XML file cannot be parsed or rewritten, the script silently skips that problem rather than stopping the whole unpacking job. When run directly from the command line, it prints either a success message or an error and exits with a failure code on errors.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker that unpacks a PowerPoint file into a folder and prepares its XML files for editing. It is useful when another tool or a person needs to inspect or modify the contents of a .pptx file as normal files.

**Data flow**: It receives the path to a PowerPoint file and the path to an output folder. It checks that the source exists and looks like a .pptx file, creates the output folder, opens the PowerPoint as a ZIP archive, and extracts its contents. It then finds all .xml and .rels files, sends each one through XML pretty-printing, then sends each one through smart-quote escaping. It returns either an ExtractionResult with the number of XML-like files processed plus a success message, or no result plus an error message.

**Call relations**: When the script is run from the command line, the bottom of the file calls this function with the two user-provided paths and prints its message. During the unpacking flow, this function delegates the two cleanup passes to _prettify_xml and _escape_smart_quotes, because extraction, formatting, and quote normalization are separate steps in the same preparation job.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper rewrites one XML file with clean, consistent indentation. It makes extracted PowerPoint XML easier for humans and text-based tools to read.

**Data flow**: It receives a file path. It tries to parse the file as XML, asks the XML library to indent the document with two spaces, converts the document back into UTF-8 XML bytes with an XML declaration, and writes those bytes back to the same file. If anything goes wrong, it leaves the file as-is and does not report an error.

**Call relations**: extract_pptx calls this helper once for each extracted .xml and .rels file before doing quote cleanup. It does not call back into the main flow; it simply updates the file in place if parsing and writing succeed.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks with XML character entities. That keeps those special quote characters explicit and stable inside the rewritten XML files.

**Data flow**: It receives a file path and reads the file as UTF-8 text. If it finds no curly single or double quotes, it changes nothing. If it does find them, it replaces each one with its matching XML entity, then writes the updated text back to the same file. If reading or writing fails, it quietly leaves the file alone.

**Call relations**: extract_pptx calls this helper after the pretty-printing pass for every extracted .xml and .rels file. In the larger flow, it is the final cleanup step before extract_pptx reports how many XML-like files were prepared.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`entrypoint` · `manual packaging / command run`

A `.pptx` PowerPoint file is really a ZIP archive full of XML files and related assets. This script is the “put it back in the box” step after someone has unpacked and possibly edited that folder. It first checks that the input is a real directory and that the requested output name ends in `.pptx`. Then it copies the whole folder into a temporary work area, so the original files are not changed directly.

Before creating the final ZIP archive, it visits every `.xml` and `.rels` file. These files often contain indentation and line-break whitespace that is useful for humans but not needed by PowerPoint. The script removes that formatting whitespace, like tidying extra blank space from a document. It is careful not to remove text inside DrawingML text elements, because that is where visible slide text can live.

Finally, it writes every file from the temporary work area into a compressed ZIP file with the `.pptx` extension. If XML cleanup fails, it prints a clear error to standard error and stops, rather than silently producing a possibly broken presentation.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a PowerPoint `.pptx` file from a directory that contains the unpacked presentation contents. It is the main worker used by the command-line script.

**Data flow**: It receives a source directory path and an output file path. It checks that the source is a directory and that the output name ends in `.pptx`; if either check fails, it returns no output path and an error message. Otherwise, it copies the source folder into a temporary workspace, asks `_condense_xml` to clean each XML-related file, creates the output folder if needed, compresses the workspace into a `.pptx` ZIP archive, and returns the created path plus a success message.

**Call relations**: When the script is run from the command line, this is the function that does the real packing work. During that work it calls `_condense_xml` for each `.xml` and `.rels` file before handing the cleaned folder contents to Python’s ZIP-writing tools to create the final presentation file.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there for formatting. It avoids touching text nodes that may contain real PowerPoint slide text.

**Data flow**: It receives the path to one XML-like file. It parses the file into an XML tree, walks through each node, removes blank-only text and tail whitespace where it is safe, skips protected text elements, then writes the XML back to the same file using UTF-8. If parsing or writing fails, it prints an error message naming the file and raises the failure so the packing process stops.

**Call relations**: This function is called only by `assemble_pptx`, after the presentation folder has been copied into a temporary workspace. It finishes its cleanup before `assemble_pptx` compresses the files, so the final `.pptx` contains the condensed XML rather than the original formatting-heavy version.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### Deck repair and slide utilities
Command-line tools for fixing generated decks and performing slide-level cleanup, insertion, and thumbnail generation.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`entrypoint` · `post-generation repair / command-line maintenance`

A `.pptx` file is really a ZIP file full of XML files. This script opens that ZIP package, looks for known bad patterns, and rewrites the package without those problems. Without it, PowerPoint may show a scary “cannot read” or “repair” dialog, or it may silently damage text by trimming spaces that were meant to stay, such as indentation in code blocks.

The script fixes three things. First, it removes fake slide master references from `[Content_Types].xml` when those slide master files do not actually exist. Second, it removes directory entries from the ZIP, because PowerPoint packaging rules expect files, not separate folder records. Third, it scans slide-related XML files for text elements whose text begins or ends with a space or tab, and adds `xml:space="preserve"`. That attribute tells PowerPoint, “do not clean up this whitespace.”

The repair is careful. It first checks whether anything needs fixing. If not, it leaves the file alone. If repairs are needed, it writes a temporary `.tmp` PowerPoint file, copies every real file into it with corrections, then replaces the original file. This is like unpacking a suitcase, removing broken labels and protecting fragile notes, then repacking it neatly.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects intentional leading or trailing spaces in PowerPoint text. It adds `xml:space="preserve"` to text runs where PowerPoint would otherwise trim spaces or tabs.

**Data flow**: It receives a dictionary of ZIP entry names mapped to their raw file bytes. It only looks at slide, layout, master, and notes XML files that can contain visible text. For each matching XML file, it parses the XML, finds DrawingML text elements, checks whether their text starts or ends with a space or tab, and adds the preserve marker when missing. It returns a dictionary containing only the changed XML files, plus a count of how many text elements were repaired.

**Call relations**: The main `repair` function calls this after reading the PowerPoint package into memory. This helper does the text-specific repair work, using XML parsing and XML writing from `lxml.etree`, then hands the updated file contents back so `repair` can include them in the rewritten `.pptx` file.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a `.pptx` file. It checks whether the file exists, finds known PowerPoint package problems, rewrites the file if needed, and reports what it changed.

**Data flow**: It receives a filename and turns it into a filesystem path. It opens the `.pptx` as a ZIP file, reads all non-folder entries, records which slide master files really exist, checks for folder entries, removes references to missing slide masters from `[Content_Types].xml`, and asks `_repair_whitespace_preservation` to fix vulnerable text XML. If no problem is found, it prints that no repairs are needed and returns `True`. If repairs are needed, it writes a corrected temporary ZIP file, replaces the original file with it, prints the number of fixes, and returns `True`. If the input file is missing, it prints an error and returns `False`.

**Call relations**: When this file is run from the command line, the bottom of the script calls `repair` with the user’s PowerPoint filename. Inside the repair process, it relies on ZIP reading and writing to rebuild the package, regular expressions to find and remove bad XML references, `_repair_whitespace_preservation` for text-space fixes, and `shutil.move` to swap the corrected temporary file into place.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command-line invocation`

A PowerPoint .pptx file is really a zip file full of XML files, images, notes, layouts, and relationship files that say which piece points to which other piece. This script helps edit that structure without opening PowerPoint. It has three jobs. The clean command looks through an unpacked PPTX folder, finds slides and resources that are no longer referenced, deletes them, and removes stale entries from the content type list so the package stays tidy. The add command either copies an existing slide or creates a blank slide tied to an existing slide layout. It also registers the new slide in the package metadata, then prints the line that still needs to be added to presentation.xml so PowerPoint will show it in the deck order. The thumbnail command takes a normal .pptx file, converts it to PDF with LibreOffice, converts the PDF pages to JPEG images, and arranges those images into one or more labeled grids. Hidden slides get gray placeholder images with an X, so the visual overview still matches the deck’s slide list. In short, this file is a practical repair-and-inspection kit for PowerPoint packages.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element. Other functions use it whenever they need to inspect a PowerPoint package file.

**Data flow**: It receives a filesystem path. It asks lxml, an XML-reading library, to parse the file, then returns the root element so callers can search or edit it.

**Call relations**: This is a shared reader used by the cleaning and slide-adding helpers. Those helpers call it before changing relationship files, content type files, or copied slide relationship files.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to disk. It keeps the package files in XML form after another function has changed them in memory.

**Data flow**: It receives an XML root element and a path. It turns the XML tree into UTF-8 bytes with an XML declaration, then writes those bytes to the target file.

**Call relations**: This is the matching writer for _parse_xml. Cleanup and add helpers call it after removing relationships, adding new package entries, or stripping notes links from cloned slides.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file that the unpacked PowerPoint package says it uses. This is the main evidence used to decide which resources are safe to delete.

**Data flow**: It receives the root folder of an unpacked PPTX. It scans every .rels relationship file, reads each relationship target, resolves it to a path inside the package when possible, and returns a set of relative paths.

**Call relations**: run_clean calls this during its cleanup loop. The result is handed to _remove_unreferenced_resources, which deletes files that are not in this referenced set.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds which slide XML files are actually part of the presentation’s slide list. This prevents the cleaner from keeping old slide files that still sit in the folder but are no longer shown in the deck.

**Data flow**: It reads presentation.xml and its relationship file. It maps relationship IDs to slide filenames, then reads the slide IDs used by the presentation and returns the slide filenames that are truly active.

**Call relations**: run_clean calls this first. Its answer is passed to _remove_orphan_slides so that only slides missing from the official deck order are removed.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special trash folder inside the unpacked package. This clears out leftovers that were deliberately parked for removal.

**Data flow**: It receives the unpacked PPTX folder. If a [trash] directory exists, it deletes files directly inside it, removes the empty folder, and returns the deleted relative paths.

**Call relations**: run_clean calls this after removing orphan slides. The returned paths are added to the final deletion report and later used when stale content type entries are stripped.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that are not in the presentation’s active slide list. It also removes their companion relationship files and cleans their entries out of presentation relationships.

**Data flow**: It receives the unpacked folder and a set of active slide filenames. It scans ppt/slides, deletes slide XML files not in that set, deletes matching .rels files, updates presentation.xml.rels if needed, and returns the paths it removed.

**Call relations**: run_clean calls it using the active slide names from _active_slide_names. When it edits presentation relationships, it uses _parse_xml to read them and _write_xml to save them.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, embedded files, charts, themes, notes, drawings, and similar PowerPoint parts. This is like removing props from backstage when no slide points to them anymore.

**Data flow**: It receives the unpacked folder and a set of referenced package paths. It checks known resource directories, deletes files absent from the referenced set, removes relationship files whose parent file is gone, and returns all deleted paths.

**Call relations**: run_clean calls this repeatedly after collecting relationship targets. It may uncover more dead files after the first pass, so run_clean loops until this function has nothing left to delete.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes content type records for files that were deleted. Content types are a package index that tells PowerPoint what kind of file each part is.

**Data flow**: It receives the unpacked folder and the list of removed paths. It opens [Content_Types].xml, removes Override entries whose part names match deleted files, and writes the XML back only if something changed.

**Call relations**: run_clean calls this at the end, after all deletion work is done. It uses _parse_xml and _write_xml so the package’s central file-type list no longer mentions missing parts.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint folder. Someone uses this when a PPTX directory has leftover slides or media that should no longer be packaged.

**Data flow**: It receives an unpacked PPTX path. It finds active slides, removes inactive slide files, clears the trash folder, repeatedly removes unreferenced resources, strips stale content type entries, and returns the full list of deleted files.

**Call relations**: _cmd_clean calls this after checking that the folder exists. Internally it coordinates the cleanup helpers in the order needed to avoid leaving broken references behind.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as slide7.xml after slide6.xml. This avoids overwriting an existing slide file.

**Data flow**: It receives the slides directory. It scans filenames matching slide<number>.xml, picks the largest number, adds one, and returns that number; if there are no slides, it returns 1.

**Call relations**: Both _create_from_layout and _clone_existing call this before writing a new slide file. It gives those functions a safe new filename.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to the package’s content type list. Without this, the PPTX may contain the slide file but not correctly describe it as a slide.

**Data flow**: It receives the unpacked folder and a slide filename. It reads [Content_Types].xml, checks whether the slide already has an Override entry, adds one if missing, and writes the file back.

**Call relations**: _create_from_layout and _clone_existing call this after creating or copying the slide file. It relies on _parse_xml and _write_xml to update the XML safely.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the presentation to the new slide. This gives the slide a relationship ID, which is the handle used elsewhere in the deck XML to refer to that slide.

**Data flow**: It receives the unpacked folder and slide filename. It reads presentation.xml.rels, returns an existing relationship ID if the slide is already listed, or creates a new rId with the next number and returns it.

**Call relations**: _create_from_layout and _clone_existing call this after making the slide. The returned relationship ID is printed with the suggested presentation.xml slide-list entry.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for presentation.xml. PowerPoint slide IDs are separate from filenames and relationship IDs.

**Data flow**: It reads presentation.xml, extracts existing slide ID numbers, returns one higher than the largest, or returns 256 if none are found.

**Call relations**: _create_from_layout and _clone_existing call this after registering the presentation relationship. They use the result when printing the XML line the user should add to the deck’s slide list.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that uses an existing slide layout. This is useful when the user wants a fresh slide with the deck’s layout styling already connected.

**Data flow**: It receives the unpacked folder and a layout filename. It checks that the layout exists, writes a blank slide XML file, writes a relationship file pointing to the layout, registers the slide in package metadata, and prints the next manual slide-list entry.

**Call relations**: run_add sends layout-based requests here. This function uses _next_slide_number, _register_content_type, _register_presentation_rel, and _next_slide_id to create a slide that fits into the PPTX structure.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Copies an existing slide to make a new slide. It removes any copied notes-slide relationship so the clone does not accidentally share speaker notes with the source slide.

**Data flow**: It receives the unpacked folder and source slide filename. It verifies the source exists, copies the slide XML and its relationship file if present, removes notes relationships from the copy, registers the new slide, and prints the XML entry needed to place it in the deck.

**Call relations**: run_add sends non-layout add requests here. It uses the shared numbering and registration helpers, plus _parse_xml and _write_xml when it needs to edit the copied relationship file.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses the right way to add a slide based on the source name. A layout filename means “make a new slide from this layout”; anything else means “copy this slide.”

**Data flow**: It receives the unpacked folder and a source string. It inspects the source name, then calls either _create_from_layout or _clone_existing; it does not return a value, but those helpers create files and print instructions.

**Call relations**: _cmd_add calls this after validating the input folder. It is the small dispatcher that routes the add command to the correct slide-building path.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a PPTX file to learn the deck’s slide order and which slides are hidden. This lets the thumbnail grid match the presentation rather than just the raw file order.

**Data flow**: It receives a .pptx path. It opens the zip, reads presentation relationships and presentation.xml, matches relationship IDs to slide filenames, and returns ordered records with each slide name and hidden status.

**Call relations**: run_thumbnail calls this before rendering images. Later, _pair_slides_with_images uses this order to attach rendered images or hidden placeholders to the correct labels.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the PowerPoint into one JPEG image per visible slide. It uses external programs because this script does not contain its own PowerPoint rendering engine.

**Data flow**: It receives a PPTX path and temporary working folder. It runs LibreOffice in headless mode to convert the presentation to PDF, then runs pdftoppm to convert PDF pages to JPEG files, and returns the resulting image paths.

**Call relations**: run_thumbnail calls this inside a temporary directory. Its rendered images are passed to _pair_slides_with_images so they can be aligned with the slide list.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a simple gray image with an X for a hidden slide. This keeps hidden slides visible in the thumbnail overview without pretending they were rendered normally.

**Data flow**: It receives image dimensions. It creates a blank gray image, draws two diagonal lines across it, and returns the image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden. The placeholder is then saved and included in the thumbnail grid.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches the ordered slide list to the rendered slide images. It also inserts placeholder images for hidden slides so the final grid tells the truth about the deck.

**Data flow**: It receives slide-order records, rendered image paths, and a work folder. It uses the first rendered image size for hidden placeholders when possible, walks through the slide list, pairs visible slides with rendered JPEGs, creates placeholder JPEGs for hidden slides, and returns path-and-label pairs.

**Call relations**: run_thumbnail calls this after rendering. It depends on _make_hidden_placeholder for hidden slides and hands the finished list to _compose_grid.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one labeled contact-sheet image from slide thumbnail items. It is the part that turns many separate slide images into one easy-to-scan overview.

**Data flow**: It receives image-and-label pairs, a column count, and a cell width. It calculates the grid size, creates a white canvas, writes each label, resizes each slide image to fit, pastes it into place, draws a thin outline, and returns the completed image.

**Call relations**: run_thumbnail calls this for each chunk of slide items. The returned image is then saved as a JPEG output file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG thumbnail grids for a PowerPoint file. This gives users a quick visual index of the deck.

**Data flow**: It receives a PPTX path, an output prefix, and a column count. It extracts slide order, renders visible slides in a temporary folder, pairs images with slide labels and hidden placeholders, splits the list into grid-sized chunks, saves each grid JPEG, and returns the saved filenames.

**Call relations**: _cmd_thumbnail calls this after checking the input file and column limit. It coordinates _extract_slide_order, _render_slide_images, _pair_slides_with_images, and _compose_grid.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line clean command. It validates the folder, runs cleanup, and prints a human-readable report.

**Data flow**: It receives parsed command-line arguments. It converts the folder text to a Path, exits with an error if it does not exist, calls run_clean, then prints either the removed files or a message that nothing was found.

**Call relations**: The argument parser attaches this function to the clean subcommand. When the script is run with clean, the main block calls it through the parsed func field.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line add command. It checks the unpacked folder and starts the slide creation or slide-copy process.

**Data flow**: It receives parsed command-line arguments. It converts the folder to a Path, exits if the folder is missing, then passes the folder and source name to run_add.

**Call relations**: build_parser wires this function to the add subcommand. It is the command-line wrapper around the lower-level run_add dispatcher.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line thumbnail command. It validates the input PPTX, applies the maximum column limit, runs thumbnail generation, and prints the created files.

**Data flow**: It receives parsed command-line arguments. It checks that the input exists and has a .pptx extension, clamps the column count if needed, calls run_thumbnail, prints saved grid paths, and exits with an error if thumbnail creation fails.

**Call relations**: build_parser wires this function to the thumbnail subcommand. It is the safety-and-reporting layer around run_thumbnail.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface for the script. It tells Python which subcommands exist, what arguments each one needs, and which function to run.

**Data flow**: It creates an ArgumentParser, adds clean, add, and thumbnail subparsers, assigns their arguments and callback functions, and returns the finished parser.

**Call relations**: When the file is run as a script, the main block calls build_parser, parses the user’s command, and then invokes the selected command function.

*Call graph*: 1 external calls (ArgumentParser).
