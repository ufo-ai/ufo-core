# PowerPoint PPTX repair, slide, and packaging scripts  `stage-11.3.5`

This stage is a toolbox for working on PowerPoint files outside the main program flow. A .pptx file is really a zipped package of many smaller files, mostly XML, which is a text format used to describe slides, layouts, images, and links. These scripts let the system open that package, make safe changes, and close it again.

unpack.py is the first step. It takes a normal .pptx file, expands it into a folder, and tidies the XML so people and tools can read it more easily. slides.py works on that unpacked folder. It can remove unused pieces, add a new slide, or make a contact sheet, which is like a page of slide thumbnails for quick review. pack.py is the final step. It cleans the XML carefully, then zips the folder back into a valid .pptx without harming slide text. repair.py is a special fixer for files made by pptxgenjs, correcting known package and text issues that PowerPoint might otherwise complain about.

## Files in this stage

### Presentation unpacking
Tools for extracting a PPTX into an editable folder structure and making its XML easier to inspect.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual command-line PPTX unpacking`

A .pptx file is really a ZIP archive: a bundled folder of XML files, images, and relationship files that PowerPoint reads as one presentation. This script opens that bundle and lays its contents out into a normal directory, like unpacking a suitcase so you can inspect every item inside.

After extraction, it looks for XML-related files: regular .xml files and .rels files, which describe links between PowerPoint parts. It then formats those files with consistent indentation, so the XML is not one long hard-to-read line. Finally, it replaces “smart quotes” — curly quote characters often produced by office software — with XML entity text such as &#x201C;. That makes the files safer and more predictable for XML editing workflows.

The main public function is extract_pptx. It checks that the input exists and really has a .pptx extension, creates the destination folder, unzips the presentation, then runs two cleanup passes over the XML files. The helper functions are deliberately forgiving: if one XML file cannot be parsed or rewritten, they silently skip the problem instead of stopping the whole unpacking job. When run directly from the command line, the script prints either a success message or an error and exits with failure if something went wrong.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker for unpacking a PowerPoint file into a directory. It checks the input, extracts the ZIP contents, cleans the XML files, and reports whether the job succeeded.

**Data flow**: It receives a path to a .pptx file and a destination folder. It turns those into filesystem paths, verifies that the source exists and has the right extension, creates the output folder, opens the PowerPoint file as a ZIP archive, and extracts everything into that folder. It then finds all .xml and .rels files, sends each one through XML pretty-printing, then sends each one through smart-quote escaping. It returns either an ExtractionResult containing the number of XML-like files processed plus a success message, or None plus an error message.

**Call relations**: When the script is run from the command line, the bottom command-line block gathers the two arguments and calls this function. Inside the unpacking flow, it hands each XML-related file first to _prettify_xml so the file becomes easier to read, then to _escape_smart_quotes so curly quote characters are normalized. It also creates the final ExtractionResult used to summarize the work.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper reformats one XML file so it is easier for people to read and edit. It adds consistent spacing and writes the XML back with a UTF-8 XML declaration.

**Data flow**: It receives the path to one XML-like file. It tries to parse the file as XML, asks the XML library to indent the document using two spaces, converts the document back into bytes, and writes those bytes over the original file. If parsing or writing fails, it leaves the file as-is and does not raise an error.

**Call relations**: extract_pptx calls this once for each .xml and .rels file found after extraction. It relies on the lxml XML library to parse, indent, and serialize the document, then uses the path object to write the result back to disk. It is the first cleanup pass before _escape_smart_quotes runs.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks in one file with XML entity text. This keeps those quote characters explicit and predictable inside XML files.

**Data flow**: It receives the path to one XML-like file. It reads the file as UTF-8 text, checks whether any curly single or double quotes are present, and if so replaces each one using a fixed lookup table. It writes the changed text back to the same file. If the file cannot be read or written, it quietly leaves it unchanged.

**Call relations**: extract_pptx calls this after _prettify_xml for every .xml and .rels file it found. It uses the shared SMART_QUOTES pattern to find the characters and QUOTE_ENTITY_MAP to decide the replacement text. This is the second cleanup pass, making the already-formatted XML safer for later editing.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### Slide editing and cleanup
Utilities for manipulating unpacked slide content, removing unused parts, adding slides, and generating slide contact sheets.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command-line invocation`

A PowerPoint .pptx file is really a zip archive full of XML files and media. This script helps edit that package safely enough for common slide tasks. Without it, other tools or people would need to manually search through PowerPoint’s internal folders, relationship files, and content-type records, which is easy to get wrong and can leave a broken presentation.

The file offers three commands. The clean command looks at what the presentation still references, then deletes slide files, media, notes, themes, and other resources that are no longer connected to anything. It also removes stale entries from PowerPoint’s content-type list, which is like the package’s table of contents.

The add command either copies an existing slide or creates a new blank slide linked to a chosen slide layout. It registers the new slide in the package, but it prints the final presentation.xml line for the caller to insert, rather than editing that slide order list itself.

The thumbnail command reads slide order from the .pptx, asks LibreOffice to render slides to PDF, converts that PDF to images, adds placeholders for hidden slides, and combines everything into one or more labeled JPEG grids. In short, this script is a practical repair bench for PowerPoint slide packages.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element. Other functions use it whenever they need to inspect or edit PowerPoint package files.

**Data flow**: It receives a file path. It opens and parses that XML file using lxml, then returns the root element so callers can search or change it.

**Call relations**: This is a shared helper used by the cleaning and slide-adding paths. Functions such as _active_slide_names, _collect_all_targets, _register_content_type, and _clone_existing call it before they examine or modify PowerPoint XML.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes a changed XML tree back to disk with a proper XML declaration. It is used after the script removes or adds entries inside PowerPoint’s internal XML files.

**Data flow**: It receives an XML root element and a destination path. It turns the XML tree into bytes and overwrites the file at that path.

**Call relations**: This is the counterpart to _parse_xml. After functions such as _remove_orphan_slides, _register_content_type, _register_presentation_rel, _strip_stale_content_types, or _clone_existing change XML in memory, they call this helper to save the change.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of all files inside the unpacked PowerPoint package that are still pointed to by relationship files. Relationship files are PowerPoint’s internal links between parts, like a wiring diagram.

**Data flow**: It receives the unpacked PowerPoint folder. It scans every .rels file, reads each relationship target, resolves it to a path inside the package, and returns the set of referenced relative paths.

**Call relations**: run_clean calls this repeatedly during cleanup. Its output tells _remove_unreferenced_resources which media, charts, themes, notes, and other parts are still in use and which can be deleted.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds which slide XML files are actually part of the presentation’s slide list. This prevents the cleaner from keeping old slide files that exist on disk but are no longer shown in the deck.

**Data flow**: It reads presentation.xml and presentation.xml.rels from the unpacked package. It matches slide relationship IDs to slide filenames, then returns the filenames that are still named in the presentation’s slide list.

**Call relations**: run_clean calls this at the start. The active slide set is passed to _remove_orphan_slides so only slides outside the real presentation order are removed.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from the package’s special [trash] folder, if that folder exists. This clears out leftovers that have already been set aside for removal.

**Data flow**: It receives the unpacked package folder. It looks for a [trash] directory, deletes files directly inside it, removes the directory, and returns the relative names of what it deleted.

**Call relations**: run_clean calls this after orphan slide removal. Its returned filenames are included in the overall deletion report and later used when stale content-type entries are stripped.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that are present in ppt/slides but are no longer listed as active slides in the presentation. It also deletes their companion relationship files.

**Data flow**: It receives the unpacked package folder and the set of active slide filenames. It removes slide XML files not in that set, removes matching .rels files, cleans matching slide links from presentation.xml.rels, and returns the relative paths deleted.

**Call relations**: run_clean calls this soon after finding active slides. It uses _parse_xml and _write_xml when it needs to update the presentation relationship file after deleting slide files.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes package resources that no relationship file points to anymore, such as unused media, charts, diagrams, themes, and notes. This is like clearing parts from a workshop shelf after checking that no finished product still uses them.

**Data flow**: It receives the unpacked package folder and a set of referenced paths. It checks known resource folders, removes files missing from the reference set, removes orphaned relationship files for deleted parents, and returns the relative paths it deleted.

**Call relations**: run_clean calls this inside a loop. Because deleting one file can make another relationship file useless, run_clean recollects references and calls this again until no more resources can be removed.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes content-type records for files that were deleted. PowerPoint uses [Content_Types].xml to know what each package part is, so stale records can confuse or dirty the package.

**Data flow**: It receives the unpacked package folder and a list of removed file paths. It opens [Content_Types].xml, removes Override entries whose part names match deleted files, and saves the file if anything changed.

**Call relations**: run_clean calls this at the end of cleanup. It uses _parse_xml to read the content-type table and _write_xml to save the cleaned version.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint folder. It removes slides and resources that the presentation no longer uses and returns a list of deleted files.

**Data flow**: It receives the unpacked package path. It finds active slides, removes orphan slides, clears [trash], repeatedly removes unreferenced resources until stable, cleans content-type entries, and returns all deleted relative paths.

**Call relations**: _cmd_clean calls this when the user runs the clean command. It coordinates the lower-level cleanup helpers in the right order so the package is left consistent.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as slide7.xml after slide1.xml through slide6.xml. This avoids overwriting an existing slide file.

**Data flow**: It receives the slides directory. It scans filenames that look like slide<number>.xml, finds the largest number, and returns one higher, or 1 if no slides exist.

**Call relations**: _create_from_layout and _clone_existing call this before writing a new slide file. It gives both add paths a safe new filename.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to PowerPoint’s content-type table if it is not already there. This tells PowerPoint that the new XML file is a slide.

**Data flow**: It receives the unpacked package folder and a slide filename. It opens [Content_Types].xml, checks for the slide’s Override entry, adds one if missing, and writes the XML back.

**Call relations**: Both _create_from_layout and _clone_existing call this after creating the slide file. It relies on _parse_xml and _write_xml to update the package metadata.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Creates or finds the relationship from the main presentation file to a slide file. This gives the slide a relationship ID, which PowerPoint uses as the internal handle for that slide.

**Data flow**: It receives the unpacked package folder and a slide filename. It reads presentation.xml.rels, reuses an existing relationship if one already points to that slide, otherwise adds a new rId number, saves the file, and returns that relationship ID.

**Call relations**: _create_from_layout and _clone_existing call this after the new slide exists. The returned ID is printed so the caller can add the slide to presentation.xml’s slide list.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for presentation.xml. This is separate from the relationship ID and is part of PowerPoint’s slide ordering data.

**Data flow**: It receives the unpacked package folder. It reads presentation.xml as text, finds existing slide ID numbers, and returns one higher, or 256 if none are found.

**Call relations**: _create_from_layout and _clone_existing call this when printing the suggested <p:sldId> line. The function does not edit presentation.xml itself; it only supplies the next safe number.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that uses an existing slide layout. A layout is a reusable design structure, such as title-and-content or blank.

**Data flow**: It receives the unpacked package folder and a layout filename. It checks that the layout exists, creates a new slide XML file and a slide relationship file pointing to the layout, registers the slide in package metadata, then prints the XML line needed to add it to the presentation’s slide list.

**Call relations**: run_add calls this when the source name looks like a slide layout file. It calls _next_slide_number, _register_content_type, _register_presentation_rel, and _next_slide_id to create and register the new slide; it exits with an error if the layout is missing.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Duplicates an existing slide file. It copies the slide and its relationships, but removes any notes-slide relationship so the duplicate does not accidentally share speaker notes.

**Data flow**: It receives the unpacked package folder and a source slide filename. It checks the source exists, copies the slide to the next slide number, copies and edits the relationship file if present, registers the copied slide, then prints the XML line needed to place it in the presentation list.

**Call relations**: run_add calls this when the source is not a slide layout name. It uses _next_slide_number, _parse_xml, _write_xml, _register_content_type, _register_presentation_rel, and _next_slide_id to make the duplicate safe and known to the package.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses which kind of slide-add operation to run: make a blank slide from a layout, or duplicate an existing slide. It is the main non-CLI entry point for the add feature.

**Data flow**: It receives the unpacked package folder and the source string from the user. If the source name looks like slideLayout*.xml it creates from a layout; otherwise it clones a slide. It does not return a value, but it writes files and prints instructions.

**Call relations**: _cmd_add calls this after validating the folder. It delegates the real work to _create_from_layout or _clone_existing based on the source name.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a .pptx file and returns the slides in presentation order, including whether each slide is hidden. This lets the thumbnail grid match what a user sees in PowerPoint’s slide list.

**Data flow**: It receives a .pptx path. It opens the zip archive, reads the presentation relationship file and presentation.xml, maps relationship IDs to slide filenames, and returns ordered records with slide name and hidden status.

**Call relations**: run_thumbnail calls this first. Later, _pair_slides_with_images uses this order to match rendered images to slide names and to insert placeholders for hidden slides.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the visible slides in a PowerPoint file into JPEG images. It does this through external programs because rendering PowerPoint accurately is outside normal Python image libraries.

**Data flow**: It receives a .pptx path and a temporary work folder. It runs LibreOffice in headless mode to convert the deck to PDF, then runs pdftoppm to convert the PDF pages to JPEG files, and returns the generated image paths.

**Call relations**: run_thumbnail calls this after reading slide order. Its images are passed to _pair_slides_with_images, which lines them up with slide names and hidden-slide placeholders.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a simple gray image with an X through it to represent a hidden slide. Hidden slides may not be rendered by the normal conversion pipeline, but the grid still needs to show that they exist.

**Data flow**: It receives image dimensions. It creates a new gray RGB image, draws two diagonal lines across it, and returns the image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden. The placeholder is saved in the temporary folder and later included in the thumbnail grid.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches the logical slide list to the rendered image files. It keeps hidden slides visible in the output by replacing them with placeholder images.

**Data flow**: It receives the slide order, the rendered JPEG paths, and a work folder. It uses the first rendered image size for placeholders when available, walks through the slide order, pairs visible slides with rendered images, creates placeholder JPEGs for hidden slides, and returns pairs of image path plus label.

**Call relations**: run_thumbnail calls this after rendering. It uses _make_hidden_placeholder for hidden slides and hands the completed labeled image list to _compose_grid.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one labeled contact sheet image from slide thumbnails. It arranges slides in rows and columns, adds labels, and draws a light outline around each slide.

**Data flow**: It receives labeled image paths, a column count, and a target cell width. It calculates cell sizes, creates a white canvas, draws each label, resizes each slide image to fit, pastes it into place, and returns the finished image.

**Call relations**: run_thumbnail calls this for each chunk of slides that should fit on one output JPEG. The returned image is then saved to disk by run_thumbnail.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG grids showing thumbnails of a PowerPoint deck. It is useful for quickly seeing slide contents and order without opening the deck interactively.

**Data flow**: It receives a .pptx path, an output prefix, and a column count. It extracts slide order, renders visible slides in a temporary folder, pairs slides with images or hidden placeholders, splits the list into grid-sized chunks, saves each grid JPEG, and returns the saved filenames.

**Call relations**: _cmd_thumbnail calls this after validating command-line input. It coordinates _extract_slide_order, _render_slide_images, _pair_slides_with_images, and _compose_grid; it exits with an error if there are no slides to show.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the clean subcommand. It validates the folder, runs cleanup, and prints a human-readable report.

**Data flow**: It receives parsed command-line arguments. It converts the folder argument to a path, exits if it does not exist, calls run_clean, then prints either the removed files or a message saying nothing was found.

**Call relations**: build_parser attaches this function to the clean subcommand. When the script is run and argparse chooses clean, the main block calls this through parsed.func.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the add subcommand. It checks the unpacked package folder and then starts the slide creation or duplication process.

**Data flow**: It receives parsed command-line arguments. It turns the folder argument into a path, exits if missing, and passes the folder plus source name to run_add.

**Call relations**: build_parser attaches this function to the add subcommand. The main script dispatches to it when the user runs slides.py add.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the thumbnail subcommand. It checks that the input is a .pptx, limits the column count, runs thumbnail generation, and prints where the grids were saved.

**Data flow**: It receives parsed command-line arguments. It validates the input file, clamps columns to the maximum, calls run_thumbnail, prints the saved grid paths, and exits with an error message if rendering fails.

**Call relations**: build_parser attaches this function to the thumbnail subcommand. The main script calls it through argparse dispatch when the user requests thumbnail generation.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Builds the command-line parser that understands the script’s three subcommands and their arguments. This is what turns user text like 'clean folder' into structured values and a function to call.

**Data flow**: It creates an argparse parser, adds clean, add, and thumbnail subcommands, defines their arguments and help text, attaches the matching command functions, and returns the completed parser.

**Call relations**: The __main__ block calls this when the script is executed directly. The returned parser chooses one of _cmd_clean, _cmd_add, or _cmd_thumbnail, which then starts the requested workflow.

*Call graph*: 1 external calls (ArgumentParser).


### Presentation repacking
Tools for tidying an edited unpacked presentation and packaging it back into a valid PPTX file.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`entrypoint` · `export/repack step`

A `.pptx` file is really a ZIP archive full of XML files and other assets. This script is the “put it back in the box” step after a presentation has been unpacked and edited as a folder. Without it, the edited folder would not become a usable PowerPoint file again.

The main flow is simple. It first checks that the input is a real directory and that the requested output name ends in `.pptx`. It then copies the whole directory into a temporary working area, so the original files are not changed directly. Next it visits every XML file and relationship file (`.rels`) and removes formatting-only whitespace. This makes the files more compact and consistent, like removing extra blank space from a form without changing the answers written in it.

One important caution is built in: DrawingML text nodes, which hold visible PowerPoint text, are protected. The script avoids stripping whitespace from those text elements because spaces there may be meaningful to the slide content.

Finally, it creates the destination folder if needed and writes all files from the temporary copy into a compressed ZIP archive with the `.pptx` name. The file can also be run directly from the command line, where it prints either a success message or an error message.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a PowerPoint `.pptx` file from a folder containing unpacked presentation contents. It is the main reusable operation behind the command-line script.

**Data flow**: It receives a source folder path and an output file path. It checks that the source is a directory and that the output path ends in `.pptx`; if either check fails, it returns no output file and an error message. If the checks pass, it copies the source folder into a temporary workspace, asks `_condense_xml` to tidy each XML and `.rels` file there, then writes every file in that workspace into a compressed `.pptx` archive. It returns the final output path and a success message.

**Call relations**: When the script is run from the command line, this is the function that does the real work after arguments are read. During its packing process, it calls `_condense_xml` for each XML-like file so the archive contains cleaned-up markup before it hands everything to Python’s ZIP writer.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there for formatting, while preserving whitespace inside actual text elements. It helps make the packed presentation cleaner without changing what appears on slides.

**Data flow**: It receives the path to one XML or `.rels` file. It parses the file into an XML tree, walks through each node, and removes text or tail whitespace when that whitespace is only indentation or line breaks. It skips protected text nodes so visible presentation text is not accidentally altered. It then writes the cleaned XML back to the same file. If parsing or writing fails, it prints an error to standard error and raises the failure so the caller knows packing did not complete safely.

**Call relations**: This function is called by `assemble_pptx` while preparing the temporary copy of the presentation folder. It does not create the final PowerPoint file itself; instead, it cleans individual XML files so `assemble_pptx` can later zip the cleaned folder into the finished `.pptx`.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### PPTX repair
Repair utilities for fixing known PowerPoint packaging and text issues after PPTX generation or repacking.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`io_transport` · `manual repair tool / command-line execution`

A `.pptx` file is really a ZIP archive full of XML files. This script opens that archive, checks for known bad patterns, and rewrites the presentation only if something needs fixing. It solves three practical problems. First, some files list slide masters in `[Content_Types].xml` even though the matching slide master files do not exist; PowerPoint may show a scary “cannot read” or “repair” dialog when it sees those phantom entries. Second, ZIP directory entries are removed because they do not belong in this kind of Office package. Third, text XML elements that begin or end with spaces or tabs need `xml:space="preserve"`; without it, PowerPoint can quietly trim those spaces, which damages things like indented code or carefully aligned text. The script works like a careful copy editor: it reads the whole package, notes what is actually present, prepares corrected XML where needed, then writes a temporary fixed ZIP and moves it over the original file. If nothing is wrong, it leaves the file untouched and prints that no repairs were needed.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects intentional leading or trailing spaces in PowerPoint text. It adds `xml:space="preserve"` to text elements where PowerPoint needs an explicit instruction not to trim whitespace.

**Data flow**: It receives a dictionary of ZIP entry names to their raw file bytes. It looks only at XML files that can contain presentation text, parses each one, scans every DrawingML text element, and checks whether the text starts or ends with a space or tab. When it finds such text without the preserve marker, it adds the marker and records the rewritten XML. It returns two things: a dictionary of only the changed files and a count of how many text elements were fixed.

**Call relations**: The main `repair` function calls this after reading the contents of the PowerPoint ZIP file. This helper uses XML parsing and XML writing from `lxml.etree` so that the larger repair flow can later replace only the entries whose text needed protection.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine. Given a `.pptx` filename, it checks the presentation package for known problems, rewrites the file if needed, and reports what happened.

**Data flow**: It takes a path-like filename as input and first confirms that the file exists. It opens the `.pptx` as a ZIP archive, reads its real file entries, notes whether there are unwanted directory entries, and records which slide master XML files actually exist. It then removes references to missing slide masters from `[Content_Types].xml`, asks `_repair_whitespace_preservation` to prepare text fixes, and decides whether any repair is necessary. If fixes are needed, it writes a temporary ZIP file containing the cleaned entries, replaces the original file with that temporary file, prints the number of fixes, and returns `True`. If the file is missing, it prints an error and returns `False`; if no fixes are needed, it returns `True` without rewriting the file.

**Call relations**: This function is used both by the command-line block at the bottom of the file and as the central coordinator for the repair process. It calls `_repair_whitespace_preservation` for the text-specific XML work, uses ZIP reading and writing to rebuild the package, uses regular expressions to detect and remove bad slide master references, and finally uses `shutil.move` to safely replace the original presentation with the repaired copy.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).
