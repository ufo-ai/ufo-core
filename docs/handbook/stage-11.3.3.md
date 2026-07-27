# PowerPoint PPTX packaging, repair, slide, and thumbnail tools  `stage-11.3.3`

This stage is a set of PowerPoint workshop tools. It is not the main app loop; it is behind-the-scenes support used when a presentation must be inspected, changed, repaired, or previewed. A .pptx file is really a zipped bundle of many smaller files, including XML files that describe slides and text. unpack.py opens that bundle into a normal folder so people or automation can edit those pieces directly. pack.py does the reverse: it gathers the folder back into a .pptx file and tidies XML spacing without damaging slide text. repair.py fixes known problems in presentations created by pptxgenjs, a library that generates PowerPoint files, so PowerPoint will not complain or alter text when opening them. slides.py is the practical toolbox: it can remove unused files from an unpacked presentation, add a slide, or create contact-sheet thumbnails for quick visual review. __init__.py simply makes these scripts importable as a package. Together they act like unpacking, fixing, organizing, and reboxing a presentation.

## Files in this stage

### Script package setup
Defines the scripts directory as an importable Python package for the PowerPoint tooling.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. In Python projects, a file with this name tells Python that the folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the system find them by name.

Here, the drawer is the `scripts` folder inside the PowerPoint document extension area. Even though this file has no code, it still matters because imports may rely on the package structure being present. Without it, some Python environments or tooling might not recognize this folder as part of the package, which could make script modules harder or impossible to import consistently.

There are no functions, classes, or configuration values here. Its job is structural: it supports the surrounding code by making the folder visible to Python’s import system.


### Presentation unpacking and slide tools
Provides tools to unpack PPTX archives and work with slide-level content, cleanup, insertion, and thumbnails.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual document unpacking / preprocessing`

A PowerPoint .pptx file is really a ZIP archive: a bundle of many files packed together. Most of the important presentation content inside it is XML, which is a text format that uses tags to describe structured data. This file opens that bundle, extracts everything into a normal directory, and then makes the XML easier and safer to edit.

The main flow is simple. First it checks that the input file exists and has the .pptx ending. Then it creates the destination folder if needed and unzips the presentation there. After that, it finds XML-related files, including .xml files and .rels relationship files, and rewrites them in a neat, consistently indented style. This is like taking a crumpled instruction sheet and laying it out clearly on a table.

Finally, it replaces curly “smart quotes” with XML entity text such as &#x201C;. That matters because these characters can be awkward when XML is later processed or compared by tools. The helper functions deliberately ignore formatting errors in individual files, so one bad XML file does not stop the whole unpacking job. When run directly from the command line, the script prints a success or error message and exits with failure if something went wrong.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker for unpacking a PowerPoint file into a folder. It checks the input, extracts the ZIP contents, tidies the XML files, and reports whether the job succeeded.

**Data flow**: It receives a path to a .pptx file and a destination directory. It turns those into filesystem paths, checks that the source exists and looks like a PowerPoint file, creates the output folder, extracts the archive, finds XML and relationship files, then sends each one through XML pretty-printing and smart-quote escaping. It returns either an ExtractionResult with the number of XML files processed plus a success message, or None plus an error message.

**Call relations**: This function is called by the command-line part of the script when a user runs the file directly. During its work it opens the presentation with zipfile.ZipFile, creates an ExtractionResult for the final count, and calls _prettify_xml and _escape_smart_quotes to clean up the extracted XML files before reporting back.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper rewrites one XML file with clean indentation and a standard UTF-8 XML declaration. It makes the extracted PowerPoint internals easier for humans and text-based tools to read.

**Data flow**: It receives the path to one XML-like file. It tries to parse the file as XML, asks the XML library to indent it with two spaces, converts it back into bytes, and writes those bytes over the original file. If parsing or writing fails, it silently leaves the file as it was.

**Call relations**: extract_pptx calls this helper once for each extracted .xml and .rels file. It relies on lxml, an XML parsing library, to understand and reformat the document before writing the cleaned version back to disk.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks in one extracted XML file with XML-safe entity text. This helps avoid trouble when later XML tools read, compare, or rewrite the file.

**Data flow**: It receives the path to one file, reads its text as UTF-8, and looks for curly single or double quotes. If none are found, it does nothing. If they are found, it substitutes each one with its matching XML entity and writes the changed text back to the same file. If reading or writing fails, it silently leaves the file unchanged.

**Call relations**: extract_pptx calls this after pretty-printing each XML-related file. It uses the file-level smart-quote pattern and replacement map, then hands back control without returning a value.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command-line invocation`

A .pptx file is really a zip file full of XML files and media. Those XML files point to each other through relationship files, a bit like a set of labeled strings connecting parts in a model. This script edits those parts directly when PowerPoint itself is not being used. The clean command looks through all relationship files, works out which slides and resources are still mentioned, and deletes leftover slides, images, themes, notes, and stale content-type entries that are no longer connected. Without this, edited presentations can accumulate broken or unused pieces. The add command either copies an existing slide or creates a blank slide tied to a chosen layout. It also updates the package records so the new slide is recognized as part of the presentation, though it prints the final presentation.xml entry for the caller to add. The thumbnail command opens a real .pptx, asks LibreOffice to render it to PDF, turns the PDF pages into JPEGs, then lays those images into one or more labeled grids. Hidden slides are shown as gray crossed-out placeholders so the overview still matches the presentation order.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so other code can inspect or edit it. It is the shared doorway into the XML files inside an unpacked PowerPoint package.

**Data flow**: It takes a filesystem path, asks lxml to parse the file, and returns the root XML element. It does not change the file on disk.

**Call relations**: The cleaning and slide-adding helpers call this whenever they need to read relationship files, content-type records, or slide relationship files before making decisions or edits.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to disk. It keeps the rest of the script from repeating the same XML serialization steps.

**Data flow**: It takes an XML root element and a destination path, converts the tree into UTF-8 XML bytes with an XML declaration, and overwrites the target file with those bytes.

**Call relations**: After helpers remove stale relationships or add new package records, they hand the changed XML tree to this function so the change becomes permanent on disk.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file that is still pointed to by a PowerPoint relationship file. This tells the cleaner which resources are still in use.

**Data flow**: It takes the unpacked PPTX directory, searches below it for .rels files, reads each relationship, resolves each target path, and returns the targets that stay inside the package as paths relative to the package root.

**Call relations**: run_clean calls this repeatedly during cleanup. Its output is passed to resource-removal logic so the script can delete only files that no relationship still mentions.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds which slide XML files are actually listed in the presentation. These are the slides PowerPoint should show, as opposed to loose files sitting in the slides folder.

**Data flow**: It reads presentation.xml and presentation.xml.rels, maps relationship IDs to slide filenames, finds the slide IDs used by the presentation, and returns the active slide filenames. If the key files are missing, it returns an empty set.

**Call relations**: run_clean calls this first, then gives the active-slide set to _remove_orphan_slides so unused slide files can be removed safely.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special temporary trash folder inside the unpacked package. This clears out leftovers that previous tooling may have intentionally set aside.

**Data flow**: It looks for a folder named [trash], deletes any files directly inside it, removes the folder, and returns the relative names of files it deleted. If the folder does not exist, it returns an empty list.

**Call relations**: run_clean calls this after orphan slide removal and records its returned filenames with the rest of the cleanup report.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that are not part of the current presentation slide list. It also removes their companion relationship files and stale presentation relationships.

**Data flow**: It receives the unpacked directory and the set of active slide names. It scans ppt/slides, deletes slide XML files not in that set, deletes matching .rels files, then edits presentation.xml.rels to remove links to deleted slides. It returns the relative paths it removed.

**Call relations**: run_clean calls this after discovering active slides. It uses _parse_xml and _write_xml when it needs to edit the presentation relationship file.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, charts, themes, drawings, embedded files, and notes. These are the non-slide parts that can be left behind after slide edits.

**Data flow**: It takes the package directory and the set of still-referenced paths. It checks known PowerPoint resource folders and removes files not in that referenced set, also removing relationship files whose parent resource file has disappeared. It returns the relative paths it deleted.

**Call relations**: run_clean calls this after _collect_all_targets. Because deleting one file can make another file unreferenced, run_clean repeats this step until no more files are removed.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes content-type records for files that were deleted. Content types are the package’s table of what kind of part each file is, so stale entries can confuse readers.

**Data flow**: It receives the unpacked directory and a list of removed file paths. It opens [Content_Types].xml, removes Override entries whose PartName matches a deleted file, and writes the XML back only if something changed.

**Call relations**: run_clean calls this at the end, after all deletions are known. It relies on _parse_xml and _write_xml to update the package metadata.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint folder. It is the main workhorse behind the clean command.

**Data flow**: It takes an unpacked PPTX directory, finds active slides, removes unused slides and trash, repeatedly removes unreferenced resources until cleanup stabilizes, updates content-type records, and returns a list of deleted paths.

**Call relations**: _cmd_clean calls this after checking that the directory exists. Inside, it coordinates the smaller cleanup helpers in the correct order so package links are checked before files are deleted.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as slide5.xml after slide4.xml. This avoids overwriting existing slide files.

**Data flow**: It scans a slides directory for filenames matching slide<number>.xml, extracts the numbers, and returns one higher than the largest number. If there are no slides, it returns 1.

**Call relations**: Both _create_from_layout and _clone_existing call this before creating a new slide file, so they know what filename to use.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to [Content_Types].xml so the PPTX package declares it as a slide. Without this record, some software may not recognize the new file correctly.

**Data flow**: It takes the unpacked directory and a slide filename, reads the content-types XML, checks whether the slide already has an Override entry, adds one if needed, and writes the file back.

**Call relations**: _create_from_layout and _clone_existing call this after creating the slide XML file. It uses _parse_xml to read the package record and _write_xml to save any new entry.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Creates or finds the relationship from the presentation to a slide. This gives the new slide a relationship ID, the label used elsewhere to refer to it.

**Data flow**: It reads ppt/_rels/presentation.xml.rels, checks whether the slide target is already listed, and returns its existing ID if so. Otherwise it chooses the next rId number, adds a slide relationship, writes the file back, and returns the new ID.

**Call relations**: _create_from_layout and _clone_existing call this after making the slide file. The returned relationship ID is printed as part of the XML snippet the user should add to presentation.xml.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for presentation.xml. PowerPoint slide IDs are separate from filenames and relationship IDs.

**Data flow**: It reads presentation.xml as text, finds existing slide ID numbers, and returns one higher than the largest. If none are found, it starts at 256, which is the usual starting range for PowerPoint slide IDs.

**Call relations**: _create_from_layout and _clone_existing call this so they can print a correct new <p:sldId> entry for the caller.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that uses an existing slide layout. This is useful when you want a fresh slide with the same master/layout styling as the deck.

**Data flow**: It takes the unpacked directory and a layout filename. It checks that the layout exists, creates a new slide XML file from a blank template, creates a relationship from that slide to the layout, registers the slide in package metadata, and prints the presentation.xml line needed to activate it. If the layout is missing, it prints an error and exits.

**Call relations**: run_add calls this when the requested source looks like a slide layout file. It relies on numbering and registration helpers to place the new slide into the package without colliding with existing files.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Copies an existing slide into a new slide file. It is a quick way to duplicate slide content and formatting inside an unpacked PowerPoint package.

**Data flow**: It takes the unpacked directory and a source slide filename. It checks that the source exists, copies the slide XML, copies its relationship file if present, removes notes-slide relationships from the copy so speaker notes are not duplicated by accident, registers the new slide, and prints the presentation.xml line needed to show it. If the source slide is missing, it prints an error and exits.

**Call relations**: run_add calls this when the source is not a slide layout name. It uses _parse_xml and _write_xml only when it needs to clean notes links out of the copied relationship file.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses how to add a slide: create one from a layout or duplicate an existing slide. It is the main workhorse behind the add command.

**Data flow**: It receives the unpacked directory and the user’s source string. If the source name looks like slideLayout*.xml, it sends the work to _create_from_layout; otherwise it sends it to _clone_existing. It does not return a value; the chosen helper writes files and prints instructions.

**Call relations**: _cmd_add calls this after validating the directory. It acts as a simple dispatcher between the two slide-creation paths.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a .pptx file to learn the presentation’s slide order and which slides are hidden. This keeps thumbnail grids aligned with what the deck actually contains.

**Data flow**: It opens the PPTX zip, reads the presentation relationship file to map relationship IDs to slide filenames, reads presentation.xml to walk the slide list in order, and returns dictionaries containing each slide name and whether it is hidden.

**Call relations**: run_thumbnail calls this before rendering images. Later, _pair_slides_with_images uses this order information to match rendered pages with slide labels and hidden-slide placeholders.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns a PowerPoint file into one JPEG image per visible slide. It uses external command-line programs because rendering PowerPoint accurately is difficult to do by hand.

**Data flow**: It takes a PPTX path and a temporary working directory. It runs LibreOffice in headless mode to convert the PPTX to PDF, then runs pdftoppm to convert that PDF into JPEG files, and returns the generated image paths sorted by name. If either conversion fails, it raises an error.

**Call relations**: run_thumbnail calls this after reading slide order. The resulting images are paired with slide names before being placed into a grid.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a gray crossed-out image to stand in for a hidden slide. This makes hidden slides visible in the overview without pretending they were rendered as normal slides.

**Data flow**: It takes desired image dimensions, creates a gray RGB image, draws two diagonal lines across it, and returns the image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden. The placeholder is saved to the temporary folder and then treated like a normal thumbnail image.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches slide-order entries with the rendered JPEGs and inserts placeholders for hidden slides. This bridges the gap between the full slide list and the renderer, which usually outputs only visible slides.

**Data flow**: It receives the slide order, rendered image paths, and a temporary directory. It uses the first rendered image to choose placeholder size if possible, walks the slide list, pairs visible slides with the next rendered image, and creates placeholder JPEGs for hidden slides. It returns a list of image-path and label pairs.

**Call relations**: run_thumbnail calls this after rendering. It hands its paired list to _compose_grid, which needs both the image file and the label to draw each cell.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one contact-sheet image from a group of slide thumbnails. It lays out labels and slide images in a clean grid.

**Data flow**: It takes image-label pairs, a column count, and a cell width. It calculates thumbnail size from the first image’s shape, creates a white canvas, draws each label, resizes each slide image to fit, pastes it into place, draws a thin outline, and returns the final image object.

**Call relations**: run_thumbnail calls this once per chunk of slides. The returned image is then saved as a JPEG grid file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG overview grids for a PowerPoint file. It is the main workhorse behind the thumbnail command.

**Data flow**: It takes a PPTX path, an output prefix, and a column count. It reads slide order, creates a temporary work folder, renders slide images, pairs them with labels and hidden placeholders, splits them into grid-sized chunks, composes each grid, saves the JPEG files, and returns the saved paths. If no slides are found, it prints an error and exits.

**Call relations**: _cmd_thumbnail calls this after validating the input file and limiting the column count. It coordinates the extraction, rendering, pairing, and drawing helpers from start to finish.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line clean action. It turns parsed command-line arguments into a cleanup run and prints a human-readable report.

**Data flow**: It receives argparse’s argument object, converts the directory string into a Path, checks that it exists, calls run_clean, and prints either the deleted files or a message saying nothing was found. If the directory is missing, it prints an error and exits.

**Call relations**: build_parser attaches this function to the clean subcommand. When the script is run and the user chooses clean, the main block calls it through the parsed command’s func field.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line add action. It validates the target unpacked PowerPoint folder and starts the slide-add process.

**Data flow**: It receives parsed arguments, converts the directory string into a Path, checks that it exists, and calls run_add with the directory and source name. If the directory is missing, it prints an error and exits.

**Call relations**: build_parser attaches this function to the add subcommand. It is the command-line wrapper around run_add.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line thumbnail action. It checks user input, enforces the maximum column count, and reports where the thumbnail grids were saved.

**Data flow**: It receives parsed arguments, checks that the input exists and ends in .pptx, clamps the requested column count to the allowed maximum, calls run_thumbnail, and prints the saved grid filenames. If validation or rendering fails, it prints an error and exits.

**Call relations**: build_parser attaches this function to the thumbnail subcommand. It wraps run_thumbnail with command-line validation and error reporting.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface for the script. It tells Python which subcommands exist, what arguments they accept, and which function should run for each one.

**Data flow**: It creates an argparse parser, adds clean, add, and thumbnail subcommands with their arguments and help text, stores the matching command function on each subcommand, and returns the parser.

**Call relations**: The script’s main block calls this when slides.py is executed directly. The returned parser reads the user’s command line and selects _cmd_clean, _cmd_add, or _cmd_thumbnail.

*Call graph*: 1 external calls (ArgumentParser).


### Repair and repackaging
Cleans known generated-PPTX formatting issues and repacks edited presentation folders into compact final PPTX files.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`domain_logic` · `post-generation repair or command-line use`

A .pptx file is really a ZIP archive full of XML files. This script opens that archive, checks for known bad patterns, and writes back a cleaned version when needed. It fixes three practical problems. First, it removes “phantom” slide master references from the file that lists the package contents; these references point to slide master files that do not actually exist, which can make PowerPoint complain that the file is damaged. Second, it removes ZIP directory entries, because the PowerPoint packaging rules expect only file entries there. Third, it protects text that starts or ends with spaces or tabs. In PowerPoint XML, those spaces can be lost unless the text element says xml:space="preserve", so the script adds that marker where needed. The script is careful not to rewrite the presentation if nothing is wrong. When repairs are needed, it writes a temporary .tmp file, copies all real files from the original archive into it with corrected content where necessary, and then replaces the original file. If the replacement fails, it deletes the temporary file rather than leaving a half-finished repair behind.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects visible spacing in slide text. It looks for PowerPoint text elements that begin or end with a space or tab, and adds the XML marker that tells PowerPoint not to trim that whitespace.

**Data flow**: It receives a dictionary of PPTX archive entries, where each key is a file path inside the ZIP and each value is that file’s bytes. It only examines XML files that can contain drawing text, such as slides, slide layouts, slide masters, and notes. For each readable XML file, it parses the XML, finds text elements, adds xml:space="preserve" when leading or trailing spaces or tabs need protection, and records the changed XML bytes. It returns a dictionary containing only the updated files, plus a count of how many text elements were fixed.

**Call relations**: The main repair flow calls this after it has read all non-directory files from the PPTX archive. This helper uses lxml.etree.fromstring to turn XML bytes into a tree it can inspect, and lxml.etree.tostring to turn changed trees back into bytes. Its results are handed back to repair, which decides whether the PPTX must be rewritten and inserts the corrected XML into the new archive.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a PPTX file. It checks whether the file exists, scans the PowerPoint ZIP package for known problems, rewrites the file if repairs are needed, and reports the outcome.

**Data flow**: It starts with a filename and turns it into a filesystem path. If the file is missing, it prints an error and returns False. Otherwise, it opens the PPTX as a ZIP file, reads all real file entries, notes whether any directory entries are present, records which slide master files actually exist, and cleans the content-types XML by removing references to missing slide masters. It also asks _repair_whitespace_preservation for any XML text fixes. If there is nothing to change, it prints that no repairs are needed and returns True. If repairs are needed, it writes a temporary ZIP file without directory entries and with corrected XML content, replaces the original PPTX with that temporary file, prints how many fixes were applied, and returns True.

**Call relations**: This function is used as the script’s command-line workhorse when the file is run directly. It relies on zipfile.ZipFile to read and write the PPTX archive, regular expressions through re.match and re.sub to find and remove bad XML references, pathlib.Path to check the input file, and shutil.move to replace the original file safely. During its scan, it calls _repair_whitespace_preservation so the whitespace-specific XML repair stays separate from the broader package-rewrite logic.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`entrypoint` · `manual packaging / command-line run`

A `.pptx` file is really a ZIP archive full of XML files and other assets. This script is the “put it back in the box” step after someone has unpacked and edited those contents as normal files and folders. Without it, the edited PowerPoint parts would remain as a directory and could not be opened as a presentation file.

The main flow checks that the input is a directory and that the output name ends in `.pptx`. It then copies the whole directory into a temporary workspace, so the original files are not edited directly. Next, it walks through XML files and relationship files (`.rels`, which describe links between PowerPoint parts) and removes formatting-only whitespace. This is like taking extra blank lines out of a recipe while leaving the actual ingredient names untouched. The script is careful not to strip text inside DrawingML text elements, because that text may be visible slide content.

Finally, it writes every file from the cleaned temporary workspace into a compressed ZIP archive with the requested `.pptx` name. The file can also be run directly from the command line, where it prints either a success message or an error and exits with failure if the inputs are wrong.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a PowerPoint file from a folder of unpacked presentation contents. It is the main operation someone would call when they want a finished `.pptx` output.

**Data flow**: It receives a source folder path and an output file path. First it checks that the source is really a folder and that the output ends in `.pptx`; if either check fails, it returns no file path and an error message. If the inputs are valid, it copies the source folder into a temporary working area, asks `_condense_xml` to clean each XML and `.rels` file, creates the output folder if needed, and writes the cleaned files into a compressed PowerPoint archive. It returns the final output path together with a human-readable success message.

**Call relations**: This is the function used by the command-line part of the script after it reads the two user arguments. During its work, it calls `_condense_xml` for each XML-like file so the contents are cleaned before being zipped, then hands the final files to the ZIP writer to create the `.pptx`.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there for formatting, while protecting real text that may appear in a slide. It keeps the PowerPoint package smaller and more consistent without changing visible content.

**Data flow**: It receives the path to one XML or relationship file. It reads and parses the XML, walks through each element, skips protected text elements, removes whitespace-only text and tail spacing elsewhere, and removes special non-normal XML nodes when needed. It then writes the cleaned XML bytes back to the same file. If parsing or writing fails, it prints an error message to standard error and raises the problem again so the packing process stops instead of silently producing a bad file.

**Call relations**: This function is called by `assemble_pptx` while preparing the temporary copy of the presentation contents. It does the careful XML cleanup step before `assemble_pptx` compresses the folder into the final `.pptx` archive.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).
