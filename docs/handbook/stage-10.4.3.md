# PowerPoint PPTX package, slide, and repair tools  `stage-10.4.3`

This stage is a set of behind-the-scenes workshop tools for PowerPoint presentations. It is used around the main presentation workflow: before editing, after editing, or when a file needs fixing. A PPTX file is really a zipped package of many XML files, where XML is a text format that stores the slides, layouts, and relationships.

The package marker file, __init__.py, simply lets Python treat the scripts folder as importable code. unpack.py opens a .pptx and spreads its contents into a folder, then formats the XML so people and tools can read it more easily. slides.py works with that unpacked material or the presentation itself: it can remove unused parts, add a slide, or render slide thumbnails like a contact sheet. pack.py does the reverse of unpack.py: it takes the folder and builds a valid .pptx again, trimming unnecessary XML spacing while preserving slide text. repair.py is the emergency kit. It fixes known package and text-spacing problems from pptxgenjs so PowerPoint opens the file cleanly.

## Files in this stage

### Script package setup
Defines the scripts directory as an importable package for PowerPoint tooling.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, it makes the `scripts` directory under the Office PowerPoint (`pptx`) skill available to the rest of the project as a structured module location.

Nothing is executed from this file, and it defines no functions, classes, or settings. Its value is organizational: without it, some Python import paths or packaging tools may not recognize this directory in the way the project expects. A simple analogy is a blank label on a filing cabinet drawer: it does not contain documents itself, but it tells the system that the drawer belongs in the filing structure.


### Presentation unpacking and slide work
Extracts PPTX packages into editable XML folders and provides slide-level cleanup, insertion, and thumbnail rendering workflows.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual document unpacking or preparation`

A .pptx file is really a ZIP archive: a bundled folder of XML files and related resources. This script opens that bundle, copies its contents into a normal directory, and then cleans up the XML so it is practical to inspect or edit. Without this kind of tool, someone working on PowerPoint internals would have to unzip the file manually and deal with hard-to-read, one-line XML files.

The main flow is simple. First, the script checks that the input file exists and has the .pptx extension. Then it creates the output folder if needed and extracts the ZIP contents there. After extraction, it finds XML files and relationship files, which use the .rels extension and describe links between parts of the PowerPoint package.

Each of those files is then passed through two cleanup steps. One step pretty-prints the XML, meaning it adds consistent line breaks and indentation, like arranging a messy recipe into readable paragraphs. The other step replaces “smart quotes” or curly apostrophes with explicit XML character entities, which are safer in XML text. The script is deliberately forgiving: if one XML file cannot be parsed or edited, it silently skips that cleanup rather than stopping the whole unpacking job.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: Unpacks a PowerPoint .pptx file into a directory and prepares its XML-like files for editing. It is the main worker used both by the command-line script and by any code that wants this unpacking behavior directly.

**Data flow**: It receives a path to a .pptx file and a destination folder. It checks that the source exists and looks like a PowerPoint file, creates the destination folder, extracts the ZIP contents, finds .xml and .rels files, formats them, escapes curly quotes, and then returns either a small result object with the number of XML files processed plus a success message, or no result plus an error message. It changes the filesystem by creating folders and writing cleaned extracted files.

**Call relations**: This is the top-level function for the file’s main job. When it needs to make XML readable, it hands each file to _prettify_xml. When it needs to make curly quotes XML-safe, it hands each file to _escape_smart_quotes. The script’s command-line section calls this function and prints the message it returns.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: Formats one XML file with consistent indentation and an XML declaration so it is easier to read and edit. It is a helper meant for internal use during extraction.

**Data flow**: It receives the path to one file. It tries to parse that file as XML, asks the XML library to indent it with two spaces, converts the document back into UTF-8 bytes, and writes those bytes over the original file. If parsing or writing fails, it leaves the file alone and does not raise an error.

**Call relations**: extract_pptx calls this after the PowerPoint archive has been unpacked and the XML-related files have been found. It does not call the quote-cleanup helper itself; instead, extract_pptx runs these cleanup stages one after the other.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: Replaces curly quotation marks and curly apostrophes in one file with XML character entities, which are explicit text codes that XML readers can safely understand. This helps avoid quote characters being misread or changed by later tools.

**Data flow**: It receives the path to one file, reads the file as UTF-8 text, and checks whether it contains any smart quotes. If none are found, it does nothing. If they are found, it substitutes each one with its matching XML entity and writes the changed text back to the same file. If reading or writing fails, it quietly leaves the file unchanged.

**Call relations**: extract_pptx calls this after the XML formatting pass. In the larger unpacking flow, it is the final cleanup step applied to each extracted XML or relationship file before the success message is returned.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command-line use for PPTX cleanup, slide creation, and thumbnail generation`

A .pptx file is really a zip file full of XML files, images, charts, and relationship files that say which pieces belong together. This script works with that inner structure. Without it, a workflow that edits PowerPoint packages by hand could leave behind broken links, unused media, or missing registration entries, which can make a presentation bloated or invalid.

The file has three main jobs. The clean command looks at the presentation’s relationship files, works out which slides and resources are still in use, deletes unused files, and removes stale entries from the content-type index. The add command either copies an existing slide or creates a blank slide tied to a chosen layout, then registers that new slide in the package metadata. It prints the final line the user still needs to add to presentation.xml, rather than editing the slide order itself. The thumbnail command converts a PPTX to a PDF using LibreOffice, turns the PDF pages into JPEG images, pairs those images with the real slide order, includes placeholders for hidden slides, and lays everything out in a neat grid.

Think of it like a careful backstage assistant for PowerPoint: it checks the wiring between parts, removes props no one uses, creates new slide parts, and can print a visual overview of the show.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its root element, so other code can inspect or change the file as structured data instead of raw text.

**Data flow**: It receives a file path. It asks the XML parser to read that file, then returns the top-level XML element found inside. It does not change the file.

**Call relations**: Many helper functions call this before they inspect relationship files, presentation metadata, content types, or copied slide relationships. It is the shared doorway from files on disk into editable XML trees.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to disk with an XML declaration and UTF-8 text encoding.

**Data flow**: It receives an XML root element and a destination path. It turns the XML tree into bytes, then replaces the file contents at that path. The result is a saved XML file; it returns nothing.

**Call relations**: Cleanup and slide-adding helpers call this after removing old links, adding new links, or stripping notes-slide references. It is the matching exit door after `_parse_xml` has let code modify XML safely.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file inside the unpacked PowerPoint folder that is referenced by a relationship file. This tells the cleaner what is still connected and should not be deleted.

**Data flow**: It receives the unpacked PPTX directory. It scans every `.rels` file, reads each relationship target, resolves it into a path relative to the package root, and returns a set of referenced paths. Targets outside the package are ignored.

**Call relations**: During `run_clean`, this is called repeatedly before removing unused resources. Each pass gives `_remove_unreferenced_resources` the current map of files that are still in use.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds which slide XML files are actually listed in the presentation’s slide order. These are the slides PowerPoint should treat as part of the deck.

**Data flow**: It receives the unpacked PPTX directory. It reads `presentation.xml.rels` to map relationship IDs to slide filenames, then reads `presentation.xml` to find which IDs appear in the slide list. It returns the filenames that are active.

**Call relations**: `run_clean` calls this first so `_remove_orphan_slides` can tell the difference between real slides and leftover slide files that are no longer part of the deck.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special trash folder inside the unpacked presentation, then removes the trash folder itself.

**Data flow**: It receives the unpacked PPTX directory. If a `[trash]` folder exists, it deletes the files directly inside it, records their relative names, removes the folder, and returns the list of deleted items. If there is no trash folder, it returns an empty list.

**Call relations**: `run_clean` calls this after orphan slide removal. It is a simple cleanup step for files that have already been set aside as disposable.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that exist on disk but are not part of the presentation’s active slide list. It also removes their companion relationship files and stale presentation links.

**Data flow**: It receives the unpacked PPTX directory and the set of active slide filenames. It walks through `ppt/slides`, deletes slide XML files not in that active set, deletes matching `.rels` files when present, and updates `presentation.xml.rels` to remove links to deleted slides. It returns the relative paths it deleted.

**Call relations**: `run_clean` calls this using the active slide names found by `_active_slide_names`. When it edits relationship XML, it uses `_parse_xml` to read and `_write_xml` to save.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, charts, diagrams, themes, and notes slides. These are the extra parts that can be left behind after slides are removed or edited.

**Data flow**: It receives the unpacked PPTX directory and a set of currently referenced paths. It checks known resource folders under `ppt`, deletes files that are not referenced, and also removes relationship files whose parent files no longer exist. It returns the relative paths it deleted.

**Call relations**: `run_clean` calls this after `_collect_all_targets`. Because deleting one file can make another file’s relationship file useless, `run_clean` repeats this step until no more resources are removed.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes entries from `[Content_Types].xml` for package parts that were deleted. This keeps the PowerPoint package’s table of contents from advertising files that no longer exist.

**Data flow**: It receives the unpacked PPTX directory and the list of removed paths. If the content-types file exists and something was removed, it deletes matching `Override` entries and writes the XML back only if it changed.

**Call relations**: `run_clean` calls this at the end, after all deletions are known. It uses `_parse_xml` and `_write_xml` to safely edit the package-level metadata.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint folder and returns a list of files it removed.

**Data flow**: It receives the unpacked PPTX directory. It finds active slides, removes inactive slide files, purges the trash folder, repeatedly deletes unreferenced resources until nothing else can be removed, then cleans stale content-type entries. It returns all deleted paths as strings.

**Call relations**: `_cmd_clean` calls this after checking that the folder exists. Inside, it coordinates the lower-level helpers in the order needed to avoid deleting live files and to catch resources that become unused after earlier deletions.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as making `slide8.xml` after seeing slides 1 through 7.

**Data flow**: It receives the slides directory. It looks for files named like `slide<number>.xml`, extracts their numbers, and returns one more than the largest number. If there are no slides, it returns 1.

**Call relations**: Both `_create_from_layout` and `_clone_existing` call this before creating a new slide file, so they do not overwrite an existing slide.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to `[Content_Types].xml`, the package file that tells PowerPoint what kind of content each part contains.

**Data flow**: It receives the unpacked PPTX directory and the new slide filename. It reads the content-types XML, checks whether the slide is already listed, and if not, adds an `Override` entry for a presentation slide. It saves the file and returns nothing.

**Call relations**: After `_create_from_layout` or `_clone_existing` writes a slide file, they call this so the PPTX package officially recognizes the new file as a slide.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the main presentation to the new slide and returns the relationship ID. A relationship ID is the short internal name PowerPoint uses to point from one XML file to another.

**Data flow**: It receives the unpacked PPTX directory and a slide filename. It reads `presentation.xml.rels`, finds the highest existing `rId` number, checks whether the slide is already linked, and otherwise adds a new slide relationship. It writes the XML and returns the new or existing ID.

**Call relations**: `_create_from_layout` and `_clone_existing` call this after creating the slide file. They use the returned ID when printing the slide-list entry the user should add to `presentation.xml`.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for the presentation’s slide list. This is separate from the filename number and the relationship ID.

**Data flow**: It receives the unpacked PPTX directory. It reads `ppt/presentation.xml`, finds existing numeric slide IDs, and returns one more than the largest one. If there are no IDs, it starts at 256, matching the common PowerPoint convention.

**Call relations**: `_create_from_layout` and `_clone_existing` call this so they can print a valid `<p:sldId>` line for adding the new slide to the presentation order.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that uses an existing slide layout, such as a title slide or content slide layout.

**Data flow**: It receives the unpacked PPTX directory and a layout filename. It checks that the layout exists, creates a new slide XML file from a blank template, writes a relationship file pointing to the chosen layout, registers the slide in package metadata, and prints the slide-list XML line the user should add. If the layout is missing, it prints an error and exits.

**Call relations**: `run_add` calls this when the requested source looks like a slide layout file. It relies on `_next_slide_number`, `_register_content_type`, `_register_presentation_rel`, and `_next_slide_id` to create a non-conflicting, properly registered slide.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Duplicates an existing slide file and its relationships, while removing any copied link to speaker notes. This avoids accidentally attaching the new slide to the old slide’s notes page.

**Data flow**: It receives the unpacked PPTX directory and a source slide filename. It checks that the source exists, picks a new slide filename, copies the slide XML, copies the relationship file if present, removes notes-slide relationships from that copy, registers the new slide, and prints the slide-list XML line to add. If the source is missing, it prints an error and exits.

**Call relations**: `run_add` calls this when the source is not a slide layout. It uses the shared numbering and registration helpers, plus `_parse_xml` and `_write_xml` when it needs to edit the copied relationships.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses the correct way to add a slide: create one from a layout or duplicate an existing slide.

**Data flow**: It receives the unpacked PPTX directory and the user’s source string. If the source name starts with `slideLayout` and ends with `.xml`, it treats it as a layout; otherwise it treats it as a slide to clone. It does not return a value; the chosen helper writes files and prints instructions.

**Call relations**: `_cmd_add` calls this after validating the folder. It is the small dispatcher that sends add requests to either `_create_from_layout` or `_clone_existing`.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a PPTX file and returns the slides in presentation order, including whether each slide is hidden.

**Data flow**: It receives the path to a `.pptx` file. It opens the zip package, reads the presentation relationship file to map IDs to slide filenames, then reads `presentation.xml` to follow the ordered slide list and hidden flags. It returns a list of dictionaries with slide names and hidden status.

**Call relations**: `run_thumbnail` calls this before rendering images, so the final thumbnail grid follows the real slide order instead of just filename order.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the visible slides in a PowerPoint file into JPEG images. It uses external programs because this script does not render PowerPoint graphics itself.

**Data flow**: It receives a PPTX path and a working directory. It asks LibreOffice (`soffice`) to convert the presentation to PDF, then asks `pdftoppm` to turn the PDF pages into JPEG files. It returns the generated image paths sorted by name. If either conversion fails, it raises an error.

**Call relations**: `run_thumbnail` calls this inside a temporary folder. The images it returns are later matched to the slide order by `_pair_slides_with_images`.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a simple gray placeholder image for a hidden slide, so hidden slides still appear in the thumbnail overview.

**Data flow**: It receives image dimensions. It creates a gray image of that size, draws an X across it, and returns the image object. It does not save the file itself.

**Call relations**: `_pair_slides_with_images` calls this whenever the slide order says a slide is hidden. The placeholder is then saved and used like a normal rendered slide image.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches slide names from the PPTX’s slide order with the rendered JPEG files. It also inserts placeholder images for hidden slides.

**Data flow**: It receives the ordered slide list, the rendered image paths, and a working directory. It uses the first rendered image to choose placeholder dimensions when possible, walks through each slide entry, pairs visible slides with the next rendered image, and creates a saved placeholder for hidden slides. It returns pairs of image path and label text.

**Call relations**: `run_thumbnail` calls this after `_extract_slide_order` and `_render_slide_images`. It prepares the exact input that `_compose_grid` needs: an image and label for every slide to show.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one contact-sheet image containing labeled slide thumbnails in rows and columns.

**Data flow**: It receives image-and-label pairs, a column count, and a thumbnail cell width. It calculates the grid size, creates a white canvas, writes each label, shrinks each slide image to fit, pastes it into place, draws a thin outline, and returns the finished image object.

**Call relations**: `run_thumbnail` calls this once for each chunk of slides that fits on a grid page. The returned image is then saved as a JPEG output file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG thumbnail grids for a PowerPoint file.

**Data flow**: It receives a PPTX path, an output prefix, and a number of columns. It reads slide order, creates a temporary work folder, renders slides to images, pairs those images with labels and hidden-slide placeholders, splits the result into chunks, composes each grid, saves the JPEG files, and returns the saved paths. If there are no slides to show, it prints an error and exits.

**Call relations**: `_cmd_thumbnail` calls this after validating the input file and column count. It coordinates the thumbnail helpers from package reading through rendering, pairing, layout, and saving.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line `clean` subcommand. It validates the folder, runs cleanup, and prints a human-readable report.

**Data flow**: It receives parsed command-line arguments. It turns the folder argument into a path, exits with an error if the folder does not exist, calls `run_clean`, then prints either the removed file list or a message saying nothing was found.

**Call relations**: The argument parser installs this as the action for the `clean` command. It is the command-line wrapper around the reusable `run_clean` function.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line `add` subcommand. It checks the unpacked PPTX folder and asks the add logic to create or duplicate a slide.

**Data flow**: It receives parsed command-line arguments. It turns the folder argument into a path, exits if it does not exist, and passes the folder and source name to `run_add`. The lower-level add code performs the file writes and prints the final instructions.

**Call relations**: The argument parser installs this as the action for the `add` command. It is the command-line wrapper around `run_add`.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line `thumbnail` subcommand. It validates the input PPTX, caps the column count, runs thumbnail creation, and prints the output paths.

**Data flow**: It receives parsed command-line arguments. It checks that the input exists and has a `.pptx` suffix, limits columns to the configured maximum, calls `run_thumbnail`, and prints the created grid files. If rendering or saving fails, it prints the error and exits.

**Call relations**: The argument parser installs this as the action for the `thumbnail` command. It is the protective command-line shell around `run_thumbnail`, catching errors and turning them into user-facing messages.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Builds the command-line interface: the top-level parser, its three subcommands, their arguments, and the function each command should run.

**Data flow**: It creates an argument parser, adds `clean`, `add`, and `thumbnail` subcommands, defines their required and optional arguments, attaches each subcommand to its command function, and returns the parser.

**Call relations**: When the script is run directly, the bottom of the file calls this function, parses the user’s command, and then runs the selected command function. It is the map that connects typed commands to the tool’s behavior.

*Call graph*: 1 external calls (ArgumentParser).


### Package repair and repacking
Repairs problematic PPTX package contents and rebuilds unpacked presentation folders into compact PowerPoint files.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`io_transport` · `manual repair / command-line maintenance`

A .pptx file is really a ZIP archive full of XML files. This script opens that archive, checks for a few known problems, and rewrites the file only if something needs fixing. First, it looks for “phantom” slide master references in the main content list. These are pointers to slide master files that do not actually exist, which can make PowerPoint complain that the file needs repair. Second, it removes folder entries from the ZIP archive, because PowerPoint’s packaging rules expect files, not separate directory records. Third, it checks text XML files for text runs that start or end with a space or tab. In PowerPoint XML, those spaces can be lost unless the text element says xml:space="preserve", so the script adds that marker where needed. The script is careful: it reads the whole presentation, decides whether repairs are needed, writes a temporary fixed copy, and then replaces the original file. If nothing is wrong, it leaves the file alone. In everyday terms, it is like checking a packed suitcase against a travel checklist: remove fake inventory items, take out empty folder labels, and add “do not trim” notes to fragile text spacing.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects meaningful spaces in PowerPoint text. It finds text elements that begin or end with a space or tab and adds the XML instruction that tells PowerPoint not to trim that whitespace away.

**Data flow**: It receives a dictionary of ZIP entry names mapped to their raw file bytes. It looks only at PowerPoint XML files that can contain visible text, parses each one as XML, searches for drawing text elements, and updates any element whose leading or trailing whitespace needs preservation. It returns a smaller dictionary containing only the changed files, plus a count of how many text elements were fixed.

**Call relations**: The main repair flow calls this after reading the .pptx archive into memory. This helper uses XML parsing and XML writing to inspect and rewrite only the affected text files, then hands the updated file contents back to repair so they can be written into the repaired presentation.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a .pptx file. It checks whether the file exists, finds the known packaging and text-spacing problems, and replaces the original presentation with a corrected version when needed.

**Data flow**: It receives a filename, turns it into a path, and stops early with an error if the file is missing. It opens the .pptx as a ZIP archive, reads its file entries, records which slide master files really exist, removes references to missing slide masters from [Content_Types].xml, asks _repair_whitespace_preservation to fix text spacing, and checks for ZIP directory entries. If no problems are found, it prints that no repairs are needed. Otherwise, it writes a temporary ZIP file containing the corrected entries, moves that temporary file over the original, prints how many fixes were applied, and returns success.

**Call relations**: This function is called by the script’s command-line block when someone runs the file directly. During the repair process it delegates the text-spacing part to _repair_whitespace_preservation, while it directly uses ZIP reading and writing, regular expression matching, and file moving to rebuild the presentation safely.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`entrypoint` · `manual packaging / command run`

A PowerPoint `.pptx` file is really a ZIP archive full of XML files and related assets. This script is the “put it back in the box” step after those files have been unpacked and possibly edited. Without it, the edited folder would not become a usable PowerPoint file again.

The main flow is simple. It first checks that the input is a directory and that the output name ends in `.pptx`. Then it copies the whole folder into a temporary workspace, so the original files are not modified directly. Inside that copy, it visits XML files and relationship files (`.rels`, which describe links between parts of the PowerPoint package) and removes formatting-only whitespace. This is like taking extra blank space out of a recipe card while leaving the actual words untouched.

One important detail is that it protects text elements used by DrawingML, the XML vocabulary PowerPoint uses for slide text and shapes. Whitespace inside those text nodes can be meaningful, so the script avoids stripping it. Finally, it creates the output folder if needed and writes every file from the cleaned workspace into a compressed ZIP archive with the `.pptx` extension. The file can also be run directly from the command line.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a PowerPoint file from an unpacked directory. It checks the inputs, cleans XML files in a safe temporary copy, then compresses the result into a `.pptx` archive.

**Data flow**: It receives a source folder path and an output file path. It turns them into path objects, verifies that the source is a directory and the destination looks like a PowerPoint file, then copies the source into a temporary working folder. It asks `_condense_xml` to clean each XML-like file, writes all files into the final ZIP-based `.pptx`, and returns the output path plus a human-readable success or error message.

**Call relations**: This is the main worker used by the command-line part of the script. During packaging, it calls `_condense_xml` for each XML or relationship file before handing the cleaned folder contents to the ZIP writer, so the final PowerPoint contains compact XML but still has the same package structure.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that only exists for formatting. It deliberately leaves actual text content alone, especially PowerPoint text nodes where spaces may matter.

**Data flow**: It receives the path to one XML-style file. It parses the file into an XML tree, walks through the elements, removes blank text and blank trailing whitespace where safe, drops unusual callable-tag child nodes, then writes the cleaned XML back to the same file using UTF-8 encoding. If parsing or writing fails, it prints a clear error message and raises the problem again.

**Call relations**: It is called by `assemble_pptx` while preparing the temporary copy of the presentation contents. Its cleaned output becomes part of the final archive that `assemble_pptx` writes, so it sits between the folder copy step and the final `.pptx` creation step.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).
