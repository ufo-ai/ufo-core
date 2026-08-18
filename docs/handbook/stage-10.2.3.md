# PowerPoint PPTX package repair and slide helpers  `stage-10.2.3`

This stage is shared behind-the-scenes support for working with PowerPoint files without using PowerPoint itself. A .pptx file is really a zipped package: a bundle of folders, XML files, images, and links that together describe the slides. These tools open that package, make safe changes, fix common problems, and close it again.

unpack.py is the “opening” tool. It turns a .pptx into a normal folder and formats the XML so people and programs can inspect it more easily. slides.py is the workbench. It can clean the unpacked folder, add a new slide, and create thumbnail contact sheets so changes can be checked visually. repair.py is the mechanic for files made by pptxgenjs, a library that creates PowerPoint documents. It fixes known packaging and text issues that may cause PowerPoint to warn, repair, or alter the file. pack.py is the “closing” tool. It rebuilds the folder into a valid .pptx and removes unnecessary XML spacing while preserving real slide text.

## Files in this stage

### Package unpacking
Extracts PPTX archives into editable folders with readable XML for inspection and modification.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual document unpacking tool`

A .pptx file looks like one file, but it is really a ZIP package full of XML files and related resources. This script opens that package, extracts everything into a normal folder, and then cleans up the XML so it is practical to edit or compare. Without a tool like this, someone working on PowerPoint internals would have to manually unzip the file and deal with dense, hard-to-read XML.

The main flow is simple. First, the script checks that the input file exists and has the .pptx ending. Then it creates the output folder if needed and extracts the ZIP contents there. After extraction, it finds files ending in .xml and .rels. A .rels file is also XML; it describes relationships between parts of the PowerPoint package, such as which slide links to which media file.

Each XML-like file is passed through a formatter that adds consistent indentation, like turning a cramped paragraph into a neatly outlined document. Then the script replaces curly “smart quotes” with XML entity text, so those characters are represented safely and consistently inside XML.

If the file is not a real ZIP archive, the script reports a clear error instead of crashing. Some cleanup steps silently skip files they cannot parse or read, which keeps the unpacking process moving even if one internal file is unusual.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker that unpacks a PowerPoint file into a folder and prepares its XML files for editing. It returns both a small result object, when successful, and a human-readable message suitable for printing to the command line.

**Data flow**: It receives the path to a .pptx file and the path to an output folder. It checks that the source exists and looks like a PowerPoint file, creates the destination folder, extracts the PPTX as a ZIP archive, finds XML and relationship files, formats them, escapes smart quotes, and then returns the number of XML files processed plus a status message. If the input is missing, has the wrong extension, or is not a valid ZIP file, it returns no result object and an error message.

**Call relations**: When the script is run from the command line, the bottom of the file calls this function with the two arguments the user typed. During its work, it calls _prettify_xml first to make each XML file readable, then _escape_smart_quotes to normalize curly quote characters. It also creates the ExtractionResult that summarizes the successful run.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper rewrites one XML file with neat indentation and a standard UTF-8 XML declaration. Someone uses it indirectly through extract_pptx to make extracted PowerPoint XML easier to read, edit, and compare.

**Data flow**: It receives a file path. It tries to parse that file as XML, asks the XML library to indent the document with two spaces, converts the XML tree back into bytes, and writes those bytes over the original file. If anything goes wrong, such as the file not being valid XML, it leaves the file as it is and does not raise an error.

**Call relations**: extract_pptx calls this once for every .xml and .rels file it finds after extraction. It does not call the quote-normalizing helper itself; it only performs the formatting stage before extract_pptx moves on to _escape_smart_quotes.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly opening and closing quotes with XML entity text. That makes quote characters explicit and consistent inside the extracted XML files.

**Data flow**: It receives a file path, reads the file as UTF-8 text, and checks for curly single or double quotes. If none are present, it changes nothing. If it finds any, it replaces each one with its matching XML entity, then writes the updated text back to the same file. If reading or writing fails, it silently leaves the file unchanged.

**Call relations**: extract_pptx calls this after the prettifying step for every XML-like file it found. In the larger unpacking flow, this is the final cleanup pass before extract_pptx reports how many files were processed.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### Slide editing helpers
Provides practical commands for cleaning unpacked PPTX folders, adding slides, and generating slide thumbnail contact sheets.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command invocation`

A PPTX file is really a zip file full of XML files, images, relationships, and other resources. This script works with that structure directly. It solves three common problems. First, after slides are removed or edited, old slide files and unused images can be left behind like forgotten boxes in a storeroom; the clean command finds what is still referenced and deletes what is not. Second, the add command creates a new slide either by copying an existing slide or by making a blank slide tied to a chosen layout. It also updates the PPTX bookkeeping files so PowerPoint can recognize the new slide. Third, the thumbnail command renders a presentation into images and lays those images out in one or more JPEG grids, which makes it easy to review a deck visually.

The file uses XML parsing for PPTX metadata, normal file operations for copying and deleting parts, LibreOffice and pdftoppm for rendering slides, and Pillow for composing the thumbnail grid. The command-line parser at the bottom connects user commands to the right workflow. An important detail is that this script expects different inputs for different jobs: clean and add work on an already-unpacked PPTX directory, while thumbnail works on a normal .pptx file.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so other code can inspect or edit it. This is the shared doorway into PPTX bookkeeping files, which are mostly XML.

**Data flow**: It takes a file path, asks lxml to parse the file, and returns the root element of the parsed XML tree. It does not change the file on disk.

**Call relations**: Many cleanup and add helpers call this when they need to read relationship files, content-type files, or copied slide relationship files before deciding what to remove or update.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML element back to disk as a complete XML file. It is used after the script changes PPTX bookkeeping data.

**Data flow**: It takes an XML root element and a destination path, turns the XML tree into UTF-8 bytes with an XML declaration, and overwrites the file at that path.

**Call relations**: Helpers that remove stale relationships or register new slide information call this after editing an XML tree created by _parse_xml.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of files inside the unpacked PPTX folder that are still pointed to by relationship files. In PPTX, relationship files act like signposts saying which parts belong together.

**Data flow**: It receives the unpacked presentation folder, scans every .rels file under it, reads each relationship target, resolves it to a path inside the folder, and returns the set of referenced relative paths.

**Call relations**: run_clean calls this repeatedly before deleting unused resources, because deleting one file can make another relationship file disappear and change what is still referenced.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds which slide XML files are actually part of the presentation's slide list. This prevents the cleaner from keeping stray slide files that exist on disk but are no longer in the deck.

**Data flow**: It reads presentation.xml and presentation.xml.rels, matches slide relationship IDs to slide filenames, then returns the slide filenames that appear in the active slide list. If the key files are missing, it returns an empty set.

**Call relations**: run_clean calls this at the start of cleanup, and _remove_orphan_slides uses the result to decide which slide files should stay.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special trash folder inside the unpacked PPTX directory. This is a final sweep for files that were intentionally set aside for removal.

**Data flow**: It receives the unpacked folder, looks for a folder named [trash], deletes any files directly inside it, removes the trash folder, and returns the relative names of files it deleted.

**Call relations**: run_clean calls this after removing orphan slides, adding its deletions to the overall cleanup report.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that are no longer listed as active slides in the presentation. It also removes their companion relationship files and cleans matching entries from the presentation relationships file.

**Data flow**: It takes the unpacked folder and the set of active slide names. It walks ppt/slides, deletes slide XML files not in that set, deletes matching .rels files, updates presentation.xml.rels if needed, and returns the relative paths it removed.

**Call relations**: run_clean calls this after learning the active slides from _active_slide_names. It uses _parse_xml and _write_xml when it must update the relationship list that points to slides.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, charts, themes, drawings, and notes slides. These are the extra parts that can linger after slide edits.

**Data flow**: It receives the unpacked folder and a set of referenced paths. It checks known PPTX resource folders, removes files that are not referenced, removes relationship files whose parent file is gone, and returns a list of deleted relative paths.

**Call relations**: run_clean calls this inside a loop after _collect_all_targets. The loop matters because removing one unused part can reveal more unused companion files on the next pass.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes content-type records for files that were deleted. A PPTX content-type file tells programs what kind of part each file is, so stale entries can confuse later tools.

**Data flow**: It takes the unpacked folder and the list of removed parts, opens [Content_Types].xml, removes Override entries whose PartName matches a removed file, and writes the XML back only if something changed.

**Call relations**: run_clean calls this at the end, after all deletions are known. It relies on _parse_xml and _write_xml to safely edit the content-type XML.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PPTX directory. It is the main programmatic entry for the clean command.

**Data flow**: It takes the unpacked folder path, finds active slides, removes orphan slides, clears the trash folder, repeatedly removes unreferenced resources until no more are found, cleans stale content-type records, and returns every deleted relative path.

**Call relations**: _cmd_clean calls this after checking that the folder exists. This function coordinates the smaller cleanup helpers in the order needed to avoid leaving broken PPTX references behind.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as slide7.xml after slide6.xml. This avoids overwriting an existing slide file.

**Data flow**: It takes the slides directory, scans filenames matching slide<number>.xml, finds the largest number, and returns one higher. If no slides exist, it returns 1.

**Call relations**: Both _create_from_layout and _clone_existing call this before writing the new slide file.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to the PPTX content-type registry if it is not already there. Without this, PowerPoint may not know that the new XML file is a slide.

**Data flow**: It takes the unpacked folder and a slide filename, opens [Content_Types].xml, checks for an existing Override entry for that slide, adds one if missing, and writes the file back.

**Call relations**: _create_from_layout and _clone_existing call this after creating the slide file, making the new part known to the package.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the presentation to the new slide and returns the relationship ID. In PPTX, the presentation points to slides through these IDs rather than raw filenames alone.

**Data flow**: It takes the unpacked folder and slide filename, opens presentation.xml.rels, finds the highest existing rId number, reuses an existing relationship if one already points to the slide, otherwise creates a new one, writes the file, and returns the rId.

**Call relations**: _create_from_layout and _clone_existing call this after making a slide so they can tell the user which relationship ID to add to presentation.xml.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID used inside presentation.xml. This is separate from the filename number and the relationship ID.

**Data flow**: It reads presentation.xml as text, extracts existing slide ID numbers, and returns one more than the largest. If none are found, it starts at 256, which is the normal lower range used by PowerPoint.

**Call relations**: _create_from_layout and _clone_existing call this so they can print the exact slide-list entry the user should add.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide connected to an existing slide layout. A layout is like a template that tells the slide where placeholders and styling should come from.

**Data flow**: It takes the unpacked folder and layout filename, verifies the layout exists, creates a new blank slide XML file, creates a relationship file pointing to the layout, registers the slide in content types and presentation relationships, then prints the XML snippet needed for the presentation slide list. If the layout is missing, it prints an error and exits.

**Call relations**: run_add calls this when the source name looks like a slide layout file. It uses the numbering and registration helpers to make the new slide fit into the PPTX structure.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Creates a new slide by copying an existing slide. It copies the slide's relationship file too, but removes notes-slide links so the duplicate does not incorrectly share speaker notes.

**Data flow**: It takes the unpacked folder and source slide filename, checks that the source exists, picks a new slide filename, copies the slide XML, copies and edits its .rels file if present, registers the new slide, and prints the slide-list XML snippet the user should add. If the source slide is missing, it prints an error and exits.

**Call relations**: run_add calls this for normal slide filenames. It calls _parse_xml and _write_xml only when it needs to remove notes relationships from the copied relationship file.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Decides whether the add command should create a slide from a layout or duplicate an existing slide. It is the main programmatic entry for adding slides.

**Data flow**: It receives the unpacked folder and the source name. If the source name looks like slideLayout*.xml, it sends the work to _create_from_layout; otherwise it sends it to _clone_existing.

**Call relations**: _cmd_add calls this after validating the unpacked folder. This function is the simple switchboard for the two add workflows.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a PPTX file and discovers the presentation's slide order, including which slides are hidden. This keeps thumbnail grids aligned with the deck order rather than raw filename order.

**Data flow**: It opens the PPTX as a zip file, reads the presentation relationship file to map relationship IDs to slide filenames, reads presentation.xml to walk the slide list, and returns ordered records containing slide names and hidden flags.

**Call relations**: run_thumbnail calls this before rendering, so later steps know how to label slides and where to insert hidden-slide placeholders.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns visible slides in a PPTX into JPEG images. It uses external tools because the script itself does not implement PowerPoint rendering.

**Data flow**: It takes a PPTX path and a temporary work folder, asks LibreOffice in headless mode to convert the deck to PDF, then asks pdftoppm to turn the PDF pages into JPEG files, and returns the generated image paths. If either conversion fails, it raises an error.

**Call relations**: run_thumbnail calls this after reading slide order. The rendered image list is later paired with slide metadata by _pair_slides_with_images.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a simple gray crossed-out image to stand in for a hidden slide. Hidden slides may not be rendered as normal pages, but the grid should still show where they are.

**Data flow**: It takes desired image dimensions, creates a gray image, draws two diagonal lines across it, and returns the Pillow image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden, then saves the placeholder into the temporary work folder.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches the ordered slide list with the rendered image files and adds placeholders for hidden slides. This creates the exact items that will appear in the thumbnail grid.

**Data flow**: It takes slide-order records, rendered image paths, and a work folder. It uses the first rendered image size for placeholder dimensions when possible, walks the slide list, pairs visible slides with the next rendered image, creates placeholder images for hidden slides, and returns image-path plus label pairs.

**Call relations**: run_thumbnail calls this after rendering. It calls _make_hidden_placeholder for hidden slides and hands the finished item list to _compose_grid.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one thumbnail contact sheet image from slide images and labels. It is like laying printed slide snapshots onto a white board in neat rows and columns.

**Data flow**: It takes image-label pairs, a column count, and a thumbnail cell width. It calculates cell sizes, creates a white canvas, draws each slide label, resizes each slide image to fit, pastes it into place, draws a thin outline, and returns the final Pillow image.

**Call relations**: run_thumbnail calls this once for each chunk of slides that should become one output JPEG.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Runs the full thumbnail-grid workflow for a PPTX file. It is the main programmatic entry for the thumbnail command.

**Data flow**: It takes a PPTX path, output prefix, and column count. It reads slide order, creates a temporary folder, renders slides to images, pairs images with labels and hidden placeholders, splits the result into grid-sized chunks, saves each grid as a JPEG, and returns the saved file paths. If no slides are found, it prints an error and exits.

**Call relations**: _cmd_thumbnail calls this after validating the input file and limiting the column count. It coordinates the extract, render, pair, and compose helpers.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the user-facing clean command. It checks the command-line input, runs cleanup, and prints a clear result.

**Data flow**: It receives parsed command-line arguments, turns the unpacked_dir string into a Path, exits with an error if it does not exist, calls run_clean, then prints either the deleted files or a message that nothing was found.

**Call relations**: build_parser connects the clean subcommand to this function. When the script is run from the command line with clean, argparse eventually calls it.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the user-facing add command. It validates the unpacked PPTX folder and starts the slide creation or duplication workflow.

**Data flow**: It receives parsed command-line arguments, converts the folder string to a Path, exits if the folder is missing, and passes the folder and source name to run_add.

**Call relations**: build_parser connects the add subcommand to this function. It is the bridge between command-line arguments and the add logic.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the user-facing thumbnail command. It checks that the input is a real .pptx file, applies the column limit, runs thumbnail creation, and prints the output files.

**Data flow**: It receives parsed command-line arguments, validates the PPTX path and suffix, caps the requested column count at the configured maximum, calls run_thumbnail, and prints the saved grid paths. If thumbnail creation raises an error, it prints that error and exits.

**Call relations**: build_parser connects the thumbnail subcommand to this function. It wraps run_thumbnail with user-friendly validation and error messages.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Creates the command-line interface for the script. It defines the available subcommands, their arguments, and which function should run for each one.

**Data flow**: It creates an argparse parser, adds clean, add, and thumbnail subcommands with their expected arguments and help text, attaches each subcommand to its command function, and returns the ready-to-use parser.

**Call relations**: The script's main block calls this when slides.py is run directly. The returned parser reads the user's command and selects _cmd_clean, _cmd_add, or _cmd_thumbnail.

*Call graph*: 1 external calls (ArgumentParser).


### Repair and repacking
Fixes known PPTX package and text issues before rebuilding the unpacked folder into a usable PowerPoint file.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`entrypoint` · `manual repair / post-generation`

A .pptx file is really a ZIP archive full of XML files. This script opens that archive, checks for known bad patterns, and rewrites the file only if something needs fixing. First, it looks for fake slide master references in [Content_Types].xml. These references point to slide master files that do not actually exist, which can make PowerPoint show a scary “cannot read” or “repair” dialog. Next, it removes directory entries from the ZIP archive, because PowerPoint packages are expected to contain files, not separate folder records. Finally, it protects text that starts or ends with spaces or tabs. In PowerPoint XML, text like indented code needs xml:space="preserve"; without it, PowerPoint may quietly trim the whitespace and change the slide. The script works like a careful photocopier: it reads every real file inside the package, fixes only the known unsafe parts, writes a temporary repaired copy, and then replaces the original file. If no problem is found, it leaves the file alone.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects text in slide-related XML files from losing important spaces or tabs at the start or end. It adds xml:space="preserve", which tells PowerPoint to keep that whitespace exactly as written.

**Data flow**: It receives a dictionary of files from inside the PowerPoint ZIP, where each name points to its raw bytes. It looks only at slide, layout, master, and notes XML files, parses each one, finds DrawingML text elements, and checks whether their text begins or ends with a space or tab. For each matching text element missing the preserve marker, it adds the marker and records the repaired XML bytes. It returns two things: a dictionary containing only the files that changed, and a count of how many text elements were fixed.

**Call relations**: The main repair flow calls this after reading the PowerPoint archive into memory. This helper does the XML-specific text cleanup, using lxml.etree.fromstring to read XML and lxml.etree.tostring to write the repaired XML back into bytes. Its results are handed back to repair, which later decides whether to rewrite those files into the final repaired .pptx.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a PowerPoint file. It checks whether the file exists, finds known pptxgenjs problems, rewrites the archive if needed, and reports whether repairs were applied.

**Data flow**: It starts with a filename and turns it into a path. If the file is missing, it prints an error and returns False. If the file exists, it opens the .pptx as a ZIP archive, reads all real files, notes whether folder entries are present, and records which slide master files actually exist. It then removes content-type references to slide masters that are not present, asks _repair_whitespace_preservation to fix whitespace-sensitive text, and checks whether anything changed. If nothing needs repair, it prints a message and returns True. If repairs are needed, it writes a temporary ZIP copy without directory entries and with corrected XML content, replaces the original file with that temporary file, prints how many fixes were applied, and returns True.

**Call relations**: This function is the center of the script. When the file is run from the command line, the bottom of the script passes the user’s .pptx path into repair. During its work it uses pathlib.Path to check the file path, zipfile.ZipFile to read and write the PowerPoint package, regular expressions to find bad XML references, _repair_whitespace_preservation for text-space fixes, and shutil.move to replace the original file with the repaired temporary copy.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`entrypoint` · `manual or scripted PPTX repack/export step`

A .pptx file is really a ZIP archive: a compressed bundle of folders, XML files, relationships, media, and other parts that PowerPoint knows how to read. This script is the “put it back in the box” step after those parts have been unpacked and possibly edited.

Before creating the final PowerPoint file, it walks through the copied contents and cleans XML files. XML often contains indentation and line breaks that make it easier for humans to read, but those spaces are not usually meaningful to PowerPoint. Removing them makes the packed file more compact and consistent. The important exception is text inside DrawingML text tags, where spaces may be part of what the user actually typed on a slide. The script deliberately protects those text nodes so it does not accidentally change visible slide content.

The main function checks that the input is a directory and the output name ends in .pptx. It copies the source into a temporary working folder, cleans every .xml and .rels file, then writes the full folder tree into a ZIP archive with the requested .pptx name. When run directly from the command line, it parses two paths, calls the packer, prints the result, and exits with an error code if packing failed.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing routine. It takes a folder that contains the unpacked parts of a PowerPoint file, cleans its XML-style files, and writes everything into a new .pptx archive.

**Data flow**: It receives a source directory path and an output file path. First it checks that the source is really a directory and that the destination ends in .pptx; if either check fails, it returns no output path and an error message. If the inputs are valid, it copies the source into a temporary folder, sends each .xml and .rels file through the XML-cleaning helper, creates the destination folder if needed, compresses all files into the final .pptx, and returns the destination path with a success message.

**Call relations**: When the script is run from the command line, the command-line wrapper calls this function with the two user-provided paths. During packing it calls _condense_xml for each XML-related file so the contents are cleaned before the ZIP archive is written.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper removes whitespace that only exists to format XML for human reading. It carefully avoids removing text from protected text elements, because that text can appear directly on a PowerPoint slide.

**Data flow**: It receives the path to one XML-like file. It parses the file into an XML tree, walks through each node, skips protected text tags, removes whitespace-only text and tail spaces elsewhere, removes special non-element nodes that do not belong in the cleaned tree, then writes the XML back to the same file using a UTF-8 XML declaration. If parsing or writing fails, it prints an error message to standard error and raises the failure again so the caller knows packing did not succeed.

**Call relations**: assemble_pptx calls this helper while preparing the temporary copy of the presentation contents. This function does the focused XML cleanup work, then hands the cleaned file back implicitly by overwriting it on disk before assemble_pptx adds it to the final .pptx archive.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).
