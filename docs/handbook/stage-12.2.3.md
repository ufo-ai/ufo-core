# PPTX Package Repair and Slide Tools  `stage-12.2.3`

This stage is a set of behind-the-scenes tools for working with PowerPoint files after they have been created or while they are being adjusted. A .pptx file is really a zipped package of many XML files and media files. These tools let the system open that package, fix it, change it, and close it again.

The unpack tool opens a .pptx into a normal folder, like emptying a suitcase so each item can be inspected. It also makes the XML easier to read and replaces risky curly quote characters with safer text forms. The pack tool does the reverse: it tidies the XML and zips the folder back into a clean .pptx file. The repair tool fixes known problems from pptxgenjs-created decks, such as packaging mistakes or text spacing that could make PowerPoint complain or display text differently. The slides tool performs practical slide operations, including removing unused package parts, adding slides, and creating thumbnail contact sheets. The __init__ file simply makes these scripts importable as a Python package.

## Files in this stage

### Package Setup and Unpacking
Package initialization and extraction tools prepare PowerPoint archives for inspection and editing.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty `__init__.py` file. In Python projects, a file with this name tells Python, and often the project’s tooling, that the surrounding folder should be treated as an importable package. Think of it like putting a label on a drawer: the label does not contain the tools, but it helps the system know the drawer belongs in the organized set.

Here, the drawer is the `scripts` folder inside the Office PowerPoint (`pptx`) document skill extension. Other files may live in this folder and provide actual script behavior. This file simply makes that folder recognizable as part of the Python module structure.

Because it is empty, nothing runs when it is imported, no settings are changed, and no functions or classes are created. Its importance is structural: without it, some Python versions, packaging tools, or import paths might not treat this directory the way the project expects.


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`io_transport` · `manual document preparation or command-line run`

A PowerPoint file is really a ZIP package full of XML files and other resources. This script opens that package, extracts everything into a chosen folder, then cleans up the XML-like files so they are easier to inspect or edit. Without this helper, someone working on PowerPoint internals would have to unzip the file by hand and deal with dense, hard-to-read XML.

The main function first checks that the input file exists and that its name ends in .pptx. It then creates the output folder if needed, opens the .pptx as a ZIP archive, and extracts all of its contents. After extraction, it looks for files ending in .xml and .rels. A .rels file is an XML relationship file used by Office documents to describe how parts of the package connect.

For each of those files, the script tries to pretty-print the XML with consistent indentation. Then it scans for “smart quotes,” the curly quotation marks often produced by word processors, and replaces them with explicit XML entity codes. This is like unpacking a suitcase, folding the clothes neatly, and labeling a few fragile items so later tools do not mishandle them. The script is forgiving: if a particular XML cleanup step fails on one file, it quietly skips that file rather than stopping the whole unpacking job.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker that unpacks a PowerPoint file into a folder and prepares its XML files for editing. Someone would use it when they need to inspect or modify the internal contents of a .pptx file.

**Data flow**: It receives the path to a .pptx file and the path to an output directory. It checks that the source exists and looks like a PowerPoint file, creates the destination folder, extracts the ZIP contents there, finds all .xml and .rels files, then sends each one through XML formatting and smart-quote escaping. It returns either a small result object with the number of XML files processed plus a success message, or no result plus an error message if the input is missing, is not a .pptx file, or is not a valid ZIP archive.

**Call relations**: This function is the central flow for the script. It may be called from the command-line section at the bottom of the file, or from another Python caller. During its work it opens the PowerPoint package with the ZIP library, creates an ExtractionResult to summarize the job, and hands each XML-related file first to _prettify_xml and then to _escape_smart_quotes.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper rewrites one XML file with clean indentation and a standard XML declaration. It exists so the unpacked PowerPoint XML is readable instead of being one dense block of text.

**Data flow**: It receives a file path. It tries to parse that file as XML, adds two-space indentation, converts the XML tree back into UTF-8 bytes with pretty printing enabled, and writes those bytes back to the same file. If parsing or writing fails, it leaves the file alone and does not raise an error.

**Call relations**: extract_pptx calls this helper once for every extracted .xml and .rels file before any quote replacement happens. It relies on the lxml XML library to parse, indent, and serialize the document, then writes the cleaned result back to disk.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks with XML entity codes. That makes those characters explicit and safer for later XML editing or comparison.

**Data flow**: It receives a file path and reads the file as UTF-8 text. If it finds no curly single or double quotes, it does nothing. If it finds them, it replaces each one with its matching XML entity string and writes the updated text back to the same file. If reading or writing fails, it silently skips the file.

**Call relations**: extract_pptx calls this helper after XML pretty-printing has already been attempted for each .xml and .rels file. The helper uses the file-level smart-quote pattern and replacement map, then hands the final text back to the filesystem by overwriting the same file.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### Slide Package Operations
Slide-level utilities clean package contents, add slides, and generate thumbnail contact sheets.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command execution`

A PowerPoint file is like a folder in a zip: slides, images, layouts, notes, and relationship files all point to each other. This script helps when those inner parts need careful editing outside PowerPoint. Without it, a user might leave behind broken or unused files, add a slide without registering it in the right XML files, or have no quick visual overview of a deck.

The tool has three subcommands. The clean command looks through an unpacked PPTX directory, finds which slide and resource files are still referenced, deletes orphaned slides and unused media-like files, and removes stale entries from the content-types list. The add command either copies an existing slide or creates a blank slide tied to a chosen layout. It also updates the package records so PowerPoint can recognize the new slide, then tells the user what still needs to be inserted into presentation.xml. The thumbnail command opens a .pptx, asks LibreOffice to render it to PDF, turns the PDF pages into JPEG slide images, and arranges those images into one or more labeled grids. Hidden slides get gray placeholder cards instead of rendered images, so the slide order remains understandable.

In short, this file is a small workshop for PPTX internals: part cleanup crew, part slide copier, part contact-sheet maker.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so other code can inspect or edit it. This is the shared doorway into the many XML files inside a PowerPoint package.

**Data flow**: It receives a file path → asks lxml, an XML-reading library, to parse that file → returns the root XML element for callers to search or modify.

**Call relations**: Cleanup and slide-adding helpers call this whenever they need to understand PowerPoint relationship files, content-type files, or copied slide relationship files. It hands parsed XML trees back to those helpers so they can make targeted changes.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to disk with an XML declaration and UTF-8 text encoding. It is used after the script has removed or added PowerPoint package records.

**Data flow**: It receives an XML root element and a destination path → turns the XML tree into bytes → replaces the file contents at that path.

**Call relations**: After helpers edit relationship or content-type XML, they call this function to save the changes. It is the matching exit door for XML data that originally came through _parse_xml.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a set of every file that is mentioned by a relationship file inside an unpacked PowerPoint folder. In plain terms, it finds the files that other files say they still need.

**Data flow**: It receives the unpacked PPTX directory → scans for every .rels file → reads each relationship target → converts each target to a path relative to the unpacked folder when it stays inside that folder → returns the set of referenced paths.

**Call relations**: run_clean calls this during its cleanup loop. The result is passed to _remove_unreferenced_resources, which uses it as the “keep list” when deciding what files are safe to delete.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds the slide XML files that are actually listed in the presentation’s slide order. This prevents the cleaner from keeping slide files that exist on disk but are no longer part of the deck.

**Data flow**: It receives the unpacked PPTX directory → reads presentation.xml.rels to map relationship IDs to slide file names → reads presentation.xml to find the relationship IDs used in the slide list → returns the matching active slide file names.

**Call relations**: run_clean calls this first. Its output becomes the active-slide list used by _remove_orphan_slides to delete slide files that PowerPoint would no longer show.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes loose files from the special [trash] folder inside the unpacked PPTX directory, then removes that folder. This clears a staging area that should not remain in the final package.

**Data flow**: It receives the unpacked PPTX directory → looks for a [trash] directory → deletes each plain file inside it → removes the trash directory → returns the paths it deleted.

**Call relations**: run_clean calls this as one cleanup step after removing orphan slides. The deleted path list is later used when cleaning stale content-type records.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide XML files that are present in the slides folder but are not in the active presentation slide list. It also removes their companion relationship files and presentation relationship entries.

**Data flow**: It receives the unpacked PPTX directory and the active slide-name set → walks through ppt/slides/slide*.xml → deletes inactive slides and their .rels files → edits presentation.xml.rels to remove relationships to those deleted slides → returns the deleted paths.

**Call relations**: run_clean calls this after _active_slide_names. When it needs to edit presentation.xml.rels, it uses _parse_xml to read the XML and _write_xml to save the updated file.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, charts, themes, notes slides, and related relationship files. This is the part of cleaning that removes baggage left behind after slides or objects were deleted.

**Data flow**: It receives the unpacked PPTX directory and a set of referenced paths → checks known resource folders under ppt → deletes files not present in the referenced set → also removes relationship files whose parent file no longer exists → returns the deleted paths.

**Call relations**: run_clean calls this repeatedly after _collect_all_targets. Because deleting one file can make another relationship file useless, run_clean loops until this function finds nothing more to remove.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes entries from [Content_Types].xml for package parts that have just been deleted. This keeps the PowerPoint package’s directory of file types from pointing at files that no longer exist.

**Data flow**: It receives the unpacked PPTX directory and the list of removed file paths → opens [Content_Types].xml if needed → removes matching Override entries → writes the XML back only if something changed.

**Call relations**: run_clean calls this at the end, after all deletions are known. It uses _parse_xml and _write_xml to make the final housekeeping change.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint directory. Someone uses it when they want to remove dead slides and unused supporting files before repacking or editing a deck.

**Data flow**: It receives an unpacked directory path → finds active slides → removes orphan slides and trash → repeatedly collects referenced files and deletes unreferenced resources → cleans stale content-type entries → returns a list of everything deleted.

**Call relations**: _cmd_clean calls this after checking that the directory exists. Internally it coordinates the smaller cleanup helpers in the right order, like a checklist for making the PPTX folder consistent again.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide file number, such as making slide8.xml after slide7.xml. This avoids overwriting existing slide files.

**Data flow**: It receives the slides directory → looks at files named like slide<number>.xml → extracts their numbers → returns one greater than the largest number, or 1 if none exist.

**Call relations**: _create_from_layout and _clone_existing call this before creating a new slide file. It gives both flows a safe new filename.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to [Content_Types].xml so the PowerPoint package knows that the file is a slide. Without this, the new slide file may exist but not be recognized correctly as a slide part.

**Data flow**: It receives the unpacked directory and new slide filename → reads [Content_Types].xml → checks whether an entry for that slide already exists → adds an Override entry if missing → writes the file back.

**Call relations**: Both _create_from_layout and _clone_existing call this after creating or copying a slide. It uses _parse_xml and _write_xml, and creates a new XML element when registration is needed.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the main presentation file to the new slide file. This gives the presentation a relationship ID, which is the internal handle PowerPoint uses to refer to the slide.

**Data flow**: It receives the unpacked directory and slide filename → reads presentation.xml.rels → reuses an existing relationship if one already points to the slide → otherwise chooses the next rId number, adds a slide relationship, saves the XML, and returns the relationship ID.

**Call relations**: _create_from_layout and _clone_existing call this after the slide file exists. The returned ID is printed for the user so it can be inserted into the presentation slide list.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Finds the next numeric slide ID for presentation.xml. This ID is separate from the filename and relationship ID; PowerPoint uses it in the slide list.

**Data flow**: It receives the unpacked directory → reads ppt/presentation.xml as text → finds existing slide ID numbers → returns one greater than the largest number, or 256 if none are found.

**Call relations**: _create_from_layout and _clone_existing call this near the end. They use the result only for the instruction they print, telling the user what slide-list entry to add.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that is linked to an existing slide layout. This is useful when a user wants a fresh slide using the deck’s built-in layout styling.

**Data flow**: It receives an unpacked directory and a layout filename → checks that the layout exists → creates a new slide XML file from a blank template → creates its relationship file pointing to the layout → registers the slide in content types and presentation relationships → prints the slide-list XML the user should add.

**Call relations**: run_add calls this when the source name looks like a slide layout file. It relies on _next_slide_number, _register_content_type, _register_presentation_rel, and _next_slide_id to make the new slide package-aware.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Copies an existing slide to a new slide file. It duplicates the slide content and relationships, but removes any notes-slide relationship so the clone does not accidentally share speaker notes.

**Data flow**: It receives an unpacked directory and source slide filename → checks that the source exists → chooses a new slide number → copies the slide XML → copies and edits the relationship file if present → registers the new slide in content types and presentation relationships → prints the slide-list XML the user should add.

**Call relations**: run_add calls this when the source is not a slide layout name. It uses file copying for the slide itself, XML parsing and writing to remove notes relationships, and the registration helpers to make the copied slide visible to the package.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses the correct way to add a slide: create one from a layout or duplicate an existing slide. It is the public add operation behind the add command.

**Data flow**: It receives an unpacked directory and a source string → inspects the source name → sends layout names to _create_from_layout and other names to _clone_existing → the chosen helper creates files, updates package records, and prints instructions.

**Call relations**: _cmd_add calls this after validating the directory. It acts as a simple router between the two slide-add workflows.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a .pptx file and returns the slides in presentation order, including whether each slide is hidden. This lets the thumbnail grid match what a user sees in PowerPoint’s slide list.

**Data flow**: It receives a .pptx path → opens it as a zip file → reads presentation relationships to map IDs to slide filenames → reads presentation.xml to walk the ordered slide list → returns dictionaries with slide names and hidden flags.

**Call relations**: run_thumbnail calls this before rendering images. Its ordered list is later matched with the rendered image files by _pair_slides_with_images.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the visible slides in a .pptx into JPEG images. It does this by asking external programs to convert the deck to PDF and then convert the PDF pages to images.

**Data flow**: It receives a .pptx path and temporary work directory → runs LibreOffice in headless mode to make a PDF → runs pdftoppm to turn PDF pages into JPEG files → returns the sorted image paths, or raises an error if conversion fails.

**Call relations**: run_thumbnail calls this inside a temporary directory. The resulting JPEG paths are passed to _pair_slides_with_images so they can be matched to the slide order.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a gray placeholder image for a hidden slide. This keeps hidden slides visible in the thumbnail overview without pretending they were rendered like normal slides.

**Data flow**: It receives image dimensions → creates a gray image → draws diagonal cross lines over it → returns the placeholder image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden. The placeholder is saved to the temporary folder and later included in the grid.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches the ordered slide list to the rendered JPEG files, adding placeholders for hidden slides. This makes the thumbnail grid line up with the true slide order.

**Data flow**: It receives slide-order entries, rendered image paths, and a work directory → learns a placeholder size from the first rendered image if possible → walks the slide order → pairs hidden slides with generated placeholders and visible slides with the next rendered image → returns path-and-label pairs.

**Call relations**: run_thumbnail calls this after rendering. It calls _make_hidden_placeholder for hidden slides and hands the final labeled items to _compose_grid.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one labeled contact sheet image from a list of slide thumbnails. It is like arranging printed photos on a page with captions underneath each one.

**Data flow**: It receives slide image paths with labels, a column count, and a cell width → calculates label space, thumbnail size, rows, and canvas size → opens each slide image, resizes it to fit, pastes it into place, draws a label and optional outline → returns the finished grid image.

**Call relations**: run_thumbnail calls this once per chunk of slides. The returned image is then saved as a JPEG output file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG thumbnail grids for a PowerPoint deck. This gives users a quick visual overview of the whole presentation without opening it slide by slide.

**Data flow**: It receives a .pptx path, output prefix, and column count → extracts slide order → creates a temporary folder → renders visible slides → pairs rendered images with slide labels and hidden-slide placeholders → splits the list into grid-sized chunks → composes and saves each grid → returns the saved file paths.

**Call relations**: _cmd_thumbnail calls this after validating the input file and limiting the column count. It coordinates the thumbnail pipeline: extract order, render, pair, compose, and save.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line clean command. It checks the user’s folder argument, runs the cleanup, and prints a human-readable report.

**Data flow**: It receives parsed command-line arguments → turns the directory string into a Path → exits with an error if it does not exist → calls run_clean → prints either the deleted files or a message that nothing was removed.

**Call relations**: build_parser attaches this function to the clean subcommand. When the script’s main command dispatcher invokes it, it hands the real cleanup work to run_clean.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line add command. It validates the unpacked PPTX folder and then starts the slide-add operation.

**Data flow**: It receives parsed command-line arguments → turns the directory string into a Path → exits with an error if missing → calls run_add with the directory and source slide or layout name.

**Call relations**: build_parser attaches this function to the add subcommand. It is the command-line wrapper around run_add.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line thumbnail command. It checks that the input is a .pptx file, enforces the maximum column count, runs thumbnail generation, and prints the output files.

**Data flow**: It receives parsed command-line arguments → validates the input path and suffix → caps columns at the configured maximum → calls run_thumbnail → prints saved grid paths, or prints an error and exits if thumbnail creation fails.

**Call relations**: build_parser attaches this function to the thumbnail subcommand. It wraps run_thumbnail with user-facing validation, warnings, and error messages.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Creates the command-line interface definition for the script. It tells Python which subcommands exist, which arguments they take, and which function should run for each one.

**Data flow**: It creates an ArgumentParser → adds clean, add, and thumbnail subcommands with their arguments → connects each subcommand to its command function → returns the configured parser.

**Call relations**: The script’s main block calls this when slides.py is run directly. The parser then reads the user’s command and dispatches to _cmd_clean, _cmd_add, or _cmd_thumbnail.

*Call graph*: 1 external calls (ArgumentParser).


### Repair and Repacking
Final package tools repair generated decks and rebuild edited folders into clean PPTX archives.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`entrypoint` · `post-generation repair or manual CLI use`

A .pptx file is really a ZIP archive full of XML files. This script opens that archive, looks for a few known bad patterns, and rewrites the file in a safer form. Without it, PowerPoint may show a “cannot read” or “repair” dialog, or it may quietly remove important spaces from text such as indented code.

The script fixes three things. First, it checks the PowerPoint content list, called [Content_Types].xml, for references to slide masters that do not actually exist. These are like entries in a table of contents pointing to missing chapters, and PowerPoint does not like them. Second, it removes directory entries from the ZIP archive, because the PowerPoint packaging rules expect only file entries there. Third, it scans slide-related XML files for text runs that begin or end with spaces or tabs. In PowerPoint XML, those spaces are only kept reliably when the text element says xml:space="preserve", so the script adds that marker where needed.

The repair is cautious. It first checks whether anything needs changing. If so, it writes a temporary replacement archive, then moves it over the original file only after the rewrite succeeds.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects meaningful leading or trailing spaces in PowerPoint text. Someone would use it when text in slides, layouts, masters, or notes might contain indentation or alignment spaces that PowerPoint would otherwise strip away.

**Data flow**: It receives a dictionary where each key is a file name inside the .pptx archive and each value is that file’s raw bytes. It ignores files that are not slide-related XML, parses the matching XML files, finds DrawingML text elements, and checks whether their text starts or ends with a space or tab. When needed, it adds xml:space="preserve" to those text elements. It returns a smaller dictionary containing only the XML files that changed, plus a count of how many text elements were fixed.

**Call relations**: The main repair flow calls this after reading all files from the PowerPoint ZIP archive. This helper does the focused XML text check, using lxml to parse and write XML, then hands the changed XML bytes and fix count back to repair so the final archive can be rebuilt with those updates.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a .pptx file. It checks whether the file exists, inspects the ZIP contents, fixes known PowerPoint compatibility problems, and replaces the original file with a cleaned version if repairs were needed.

**Data flow**: It takes a file name, turns it into a path, and stops early with an error message if the file is missing. It opens the .pptx as a ZIP archive, reads all non-directory files, records which slide master files really exist, and checks whether the archive contains directory entries. It then edits [Content_Types].xml to remove references to missing slide masters and asks _repair_whitespace_preservation to find text-spacing fixes. If nothing is wrong, it prints that no repairs are needed and returns True. If repairs are needed, it writes a temporary ZIP archive without directory entries and with corrected XML, moves that temporary file over the original, prints how many fixes were applied, and returns True.

**Call relations**: This function is called by the script’s command-line block when a user runs the file with a .pptx path. During its work it delegates the text-space problem to _repair_whitespace_preservation, while it directly performs the ZIP rewriting and missing slide-master cleanup. It relies on standard path, regular expression, ZIP, and file-moving tools to inspect and replace the presentation safely.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`entrypoint` · `manual packaging or command-line repack step`

A `.pptx` PowerPoint file is really a ZIP archive full of XML files and related assets. This script is the “put it back in the box” tool: given a folder that contains the unpacked contents of a presentation, it rebuilds a valid `.pptx` archive.

Before zipping the files, it walks through the copied presentation contents and condenses XML files. In plain terms, it removes formatting-only whitespace, such as indentation and line breaks that exist just to make XML easier for humans to read. That helps produce cleaner output. It is careful not to remove real text from DrawingML text elements, where whitespace may be part of what the user sees on a slide.

The script works on a temporary copy of the input folder rather than changing the original folder directly. That is like photocopying a document before marking it up: the source stays safe. After cleanup, it creates the output folder if needed, writes every file into a ZIP archive, and gives the archive a `.pptx` name.

It can be used from the command line, and it reports a clear error if the input is not a directory or the output file does not end in `.pptx`.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a PowerPoint `.pptx` file from a directory of unpacked presentation contents. It is the main workflow: check the inputs, clean the XML, and zip everything into the final file.

**Data flow**: It receives a source directory path and an output file path. First it checks that the source is really a folder and that the destination ends in `.pptx`; if either check fails, it returns no output path and an error message. If the inputs are valid, it copies the source into a temporary working folder, asks `_condense_xml` to clean each `.xml` and `.rels` file, creates the destination folder if needed, writes all files into a compressed ZIP archive, and returns the finished path plus a success message.

**Call relations**: This is the function used by the command-line part of the script after reading the user’s arguments. During its workflow it calls `_condense_xml` for each XML-like file so that cleanup happens before the ZIP archive is created. It also relies on standard library tools for temporary folders, copying directories, path handling, and ZIP writing.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there for formatting. It protects actual text content so visible words in a PowerPoint slide are not accidentally changed.

**Data flow**: It receives the path to one XML-related file. It parses the file into an XML tree, visits each node, skips protected text nodes, removes blank-only text and tail whitespace where safe, removes unusual callable-tag child nodes, then writes the XML back to the same file using UTF-8 with an XML declaration. If parsing or writing fails, it prints an error message to standard error and raises the failure again so the packing process stops.

**Call relations**: This function is called by `assemble_pptx` while preparing the temporary copy of the presentation contents. It hands the cleaned XML file back in place, so `assemble_pptx` can then include that cleaned version in the final `.pptx` archive.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).
