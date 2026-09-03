# Office PPTX presentation utilities  `stage-17.3`

This stage is shared behind-the-scenes support for working with PowerPoint presentations. A `.pptx` file is really a zipped bundle of many smaller files, mostly XML, which is a text format that stores structured data. These utilities let the system open that bundle, edit it, put it back together, and fix common problems.

`unpack.py` is the front door for inspection and editing. It turns a `.pptx` into a normal folder and formats the XML so people and tools can read it more easily. After changes are made, `pack.py` reverses the process: it compresses the folder back into a usable `.pptx` and removes extra XML whitespace without changing the visible slide text. `repair.py` is a cleanup step for generated decks, fixing known issues that might make PowerPoint show repair warnings or lose important spaces. `slides.py` provides practical slide operations, such as removing unused files, adding a slide, or making thumbnail contact sheets. `__init__.py` simply makes these scripts importable by other project code.

## Files in this stage

### Package scaffold
Marks the PowerPoint scripts directory as an importable Python package.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, it sits inside the `office-pptx/scripts` area, which suggests this folder contains script code related to working with PowerPoint files. Even though this file has no functions or classes, it still matters because imports may rely on the folder being recognized as a package. Without it, some Python environments or tooling might not find modules in this folder in the expected way. Think of it like a label on a drawer: the label does not contain the tools, but it tells the system that the drawer is part of the organized workspace.


### Packaging and repair
Rebuilds edited presentation folders into PPTX archives and fixes known generated-file issues.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`io_transport` · `packaging/export`

A `.pptx` PowerPoint file is really a ZIP archive full of XML files and related assets. This script is the “put it back in the box” step: it takes a directory that contains those unpacked pieces, tidies the XML, and compresses everything into a `.pptx` file that PowerPoint can open.

The script first checks two simple safety rules: the input must be a real directory, and the output name must end in `.pptx`. It then copies the whole input folder into a temporary working area. That copy matters because the script rewrites XML files while cleaning them, and it avoids changing the original unpacked directory.

For every `.xml` and `.rels` file, it removes formatting-only whitespace. This is like taking extra blank spaces out of a recipe while leaving the ingredient names untouched. It is careful around DrawingML text nodes, which are where visible PowerPoint text can live, because spaces there may be meaningful to the user.

Finally, it creates the destination folder if needed and writes every file from the temporary working copy into a ZIP archive using PowerPoint’s `.pptx` extension. If XML cleanup fails, it prints a clear error message and stops rather than silently producing a bad presentation.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main packing function. It takes a folder containing unpacked PowerPoint contents and creates a `.pptx` ZIP archive from it, after cleaning XML whitespace.

**Data flow**: It receives a source directory path and an output file path. It checks that the source is a directory and that the output ends in `.pptx`; if either check fails, it returns no file path and an error message. If the inputs are valid, it copies the source into a temporary folder, asks `_condense_xml` to clean every XML and relationship file, then writes all files into the output archive. It returns the finished output path and a success message.

**Call relations**: This function is the top-level worker used by the command-line block at the bottom of the file. During its packing flow, it calls `_condense_xml` once for each XML-like file so the content is cleaned before it is zipped into the final PowerPoint file.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that exists only for formatting. It protects actual text nodes so that visible slide text is not accidentally changed.

**Data flow**: It receives the path to one XML or `.rels` file. It parses the file into an XML tree, walks through each node, removes text and tail whitespace that is only indentation or line breaks, and skips protected text tags where spaces may matter. It then writes the cleaned XML back to the same file. If something goes wrong, it prints an error message to standard error and raises the failure so the caller knows packing did not complete safely.

**Call relations**: It is called by `assemble_pptx` while preparing the temporary copy of the unpacked presentation. Its cleaned output becomes the version of the XML that `assemble_pptx` later places inside the final `.pptx` archive.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`io_transport` · `post-generation repair or command-line maintenance`

A `.pptx` file is really a ZIP archive full of XML files. This script opens that archive, looks for a few known kinds of bad or risky content, and rewrites the file if repairs are needed. Without this step, PowerPoint may show a scary “cannot read” or “repair” dialog, or it may quietly change the presentation by removing leading or trailing spaces from text.

The script fixes three things. First, it removes “phantom” slide master references from `[Content_Types].xml`. A slide master is a template-like part of a PowerPoint file; if the file claims one exists but the matching XML file is missing, PowerPoint can complain. Second, it removes ZIP directory entries, which are folder markers inside the ZIP. These are normal in many ZIP files, but they do not belong in this kind of Office package. Third, it checks text XML files and adds `xml:space="preserve"` to text runs that begin or end with a space or tab, telling PowerPoint not to trim those spaces.

The repair process is cautious. It first reads the presentation, decides whether anything needs changing, and exits cleanly if not. If changes are needed, it writes a temporary replacement archive and then moves it over the original. This is like copying a damaged binder into a clean new binder, leaving out bad divider tabs and fixing labels as you go.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects text that intentionally starts or ends with spaces or tabs. It adds an XML instruction that tells PowerPoint to preserve those spaces instead of silently trimming them.

**Data flow**: It receives a dictionary of ZIP entry names mapped to their raw file bytes. It looks only at XML files that can contain PowerPoint text, parses each one, finds DrawingML text elements, and checks whether their text begins or ends with a space or tab. For each affected text element missing the preserve marker, it adds `xml:space="preserve"`. It returns a smaller dictionary containing only the XML files that were changed, plus a count of how many text elements were repaired.

**Call relations**: The main `repair` function calls this after it has read the contents of the `.pptx` archive. This helper does the detailed XML text inspection and hands back the changed files so `repair` can place those corrected versions into the rewritten presentation.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a PowerPoint file. It checks whether the file exists, inspects the `.pptx` archive for known problems, rewrites the archive if needed, and reports what it fixed.

**Data flow**: It takes a filename, turns it into a path, and stops with an error message if the file is missing. It opens the `.pptx` as a ZIP archive, reads the real files inside it, records which slide master XML files actually exist, and checks whether the ZIP contains directory entries. It also edits `[Content_Types].xml` in memory to remove slide master references that point to missing files, then asks `_repair_whitespace_preservation` to find text spacing fixes. If there is nothing to change, it prints that no repairs are needed and returns `True`. If repairs are needed, it writes a new temporary ZIP without directory entries and with the corrected XML content, moves that temporary file over the original, prints the number of fixes, and returns `True`.

**Call relations**: This function is used both by the command-line block at the bottom of the file and as the coordinator for the whole repair job. It calls `_repair_whitespace_preservation` for the text-specific XML fix, uses regular expressions to find broken slide master references, uses `zipfile.ZipFile` to read and write the `.pptx` package, and uses `shutil.move` to replace the original file only after the repaired archive has been built.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### Presentation commands
Provides command-line tools for manipulating slides and unpacking PPTX files into editable folders.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command execution`

A PowerPoint file is really a zipped folder full of XML files, images, charts, and relationship files that point from one part to another. This script works at that folder-and-XML level. Without careful updates, a deck can keep dead images, broken slide links, or missing registration entries that PowerPoint expects.

The script offers three commands. The `clean` command looks through an unpacked `.pptx` directory, finds which slides are actually listed in the presentation, removes slide files that are no longer used, deletes leftover resources such as media or charts that nothing points to, and removes stale entries from `[Content_Types].xml`, which is PowerPoint’s table of contents for file types.

The `add` command creates a new slide either by copying an existing slide or by making a blank slide connected to an existing slide layout. It also registers the new slide in the relationship files so PowerPoint can find it. One important detail: it prints the XML line that still needs to be added to `presentation.xml`; it does not insert that slide-order entry itself.

The `thumbnail` command turns a `.pptx` into a grid image. It uses external tools, LibreOffice (`soffice`) and `pdftoppm`, to render slides, then uses Pillow, an image library, to arrange those slide pictures into labeled grids. Hidden slides get gray placeholder images so the thumbnail sheet still reflects the deck’s true slide order.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its root element, which is the top node of the XML tree. Other parts of this script use it whenever they need to inspect or edit PowerPoint’s internal XML files.

**Data flow**: It takes a file path → asks `lxml`, an XML parsing library, to read that file → returns the root XML element so callers can search or change it.

**Call relations**: This is a shared helper used by the cleaning and slide-adding steps. Functions such as `_active_slide_names`, `_collect_all_targets`, `_register_content_type`, and `_clone_existing` call it before they inspect or modify PowerPoint metadata.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to disk. It makes sure the saved file includes an XML declaration and uses UTF-8 text encoding.

**Data flow**: It takes an XML root element and a destination path → converts the XML tree into bytes → writes those bytes to the file, replacing the previous content.

**Call relations**: This helper is called after another function changes XML in memory. For example, `_remove_orphan_slides` uses it after deleting slide relationships, and `_register_content_type` uses it after adding a new slide type entry.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of all files that are still pointed to by PowerPoint relationship files. In PowerPoint packages, relationship files are like labels with arrows saying, “this slide uses that image” or “this presentation uses that slide.”

**Data flow**: It takes the unpacked PowerPoint folder → scans every `.rels` relationship file inside it → reads each target path → keeps only targets that resolve inside the same package → returns the referenced paths relative to the unpacked folder.

**Call relations**: During `run_clean`, this function provides the current map of files that are still in use. `run_clean` then passes that map to `_remove_unreferenced_resources`, which deletes files not found in the map.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds the slide files that are actually part of the presentation’s slide list. This prevents the cleaner from keeping slide files that exist on disk but are no longer used in the deck.

**Data flow**: It reads `ppt/presentation.xml` and `ppt/_rels/presentation.xml.rels` → matches slide relationship IDs to slide file names → looks for the slide IDs currently listed in the presentation → returns the active slide file names.

**Call relations**: `run_clean` calls this first when cleaning a deck. Its result is handed to `_remove_orphan_slides`, which removes slide XML files that are not in the active set.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from the special `[trash]` folder inside an unpacked PowerPoint directory. This is a project-specific cleanup step for files that were intentionally set aside for removal.

**Data flow**: It takes the unpacked folder → checks for a `[trash]` directory → deletes files directly inside that directory → removes the directory itself → returns the deleted file paths.

**Call relations**: `run_clean` calls this after orphan slides are removed. Its deleted paths are added to the same cleanup report returned to the command-line user.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Removes slide files that exist in `ppt/slides` but are not listed as active slides in the presentation. These are like loose pages left in a binder pocket: present in the folder, but not part of the actual deck.

**Data flow**: It takes the unpacked folder and the set of active slide names → checks each `slide*.xml` file → deletes inactive slide files and their matching relationship files → also removes dead slide relationships from `presentation.xml.rels` → returns the paths it deleted.

**Call relations**: `run_clean` calls this using the active slide names from `_active_slide_names`. When it edits the presentation relationship XML, it relies on `_parse_xml` to read it and `_write_xml` to save it.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes resource files that no relationship file points to anymore, such as unused media, charts, diagrams, themes, and notes slides. This keeps an unpacked PowerPoint folder from carrying dead baggage.

**Data flow**: It takes the unpacked folder and a set of referenced paths → walks known PowerPoint resource directories → deletes files whose relative paths are not in the referenced set → also removes relationship files whose parent resource file is gone → returns the deleted paths.

**Call relations**: `run_clean` repeatedly calls this after `_collect_all_targets`. Because deleting one unused file may make another relationship file obsolete, `run_clean` loops until this function has nothing more to remove.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes old entries from `[Content_Types].xml` for files that were deleted. `[Content_Types].xml` tells PowerPoint what each internal file is, so stale entries can leave the package inconsistent.

**Data flow**: It takes the unpacked folder and the list of removed file paths → reads `[Content_Types].xml` if it exists → removes any override entry whose part name matches a deleted file → writes the XML back only if it changed.

**Call relations**: `run_clean` calls this at the end, after all deletions are known. It uses `_parse_xml` and `_write_xml` to update the package’s file-type registry.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint directory. It is the main worker behind the `clean` command.

**Data flow**: It takes a folder path → finds active slides → removes inactive slide files → empties the trash folder → repeatedly removes unreferenced resources until no more can be found → cleans stale content-type records → returns a list of everything deleted.

**Call relations**: `_cmd_clean` calls this after checking that the folder exists. `run_clean` coordinates the smaller cleanup helpers and gives `_cmd_clean` the deletion list to print for the user.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Finds the next available slide file number, such as choosing `slide8.xml` after seeing slides 1 through 7. This avoids overwriting an existing slide file.

**Data flow**: It takes the slides directory → looks for files named like `slide<number>.xml` → extracts their numbers → returns one greater than the largest number, or 1 if none exist.

**Call relations**: Both `_create_from_layout` and `_clone_existing` call this before creating a new slide file. It gives those functions a safe new filename.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to PowerPoint’s content-type registry if it is not already listed. This tells PowerPoint that `/ppt/slides/slideN.xml` is a slide file.

**Data flow**: It takes the unpacked folder and a slide filename → reads `[Content_Types].xml` → checks whether the slide already has an override entry → adds one if missing → writes the updated XML back.

**Call relations**: `_create_from_layout` and `_clone_existing` call this after creating or copying a slide. It uses `_parse_xml` to read the registry and `_write_xml` to save the added entry.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the presentation to the new slide, or reuses an existing one if it is already present. This is what lets `presentation.xml` refer to the slide by a relationship ID such as `rId12`.

**Data flow**: It takes the unpacked folder and a slide filename → reads `presentation.xml.rels` → finds the highest existing relationship number → returns the existing ID if the slide is already registered, otherwise creates a new relationship entry → writes the XML back → returns the relationship ID.

**Call relations**: `_create_from_layout` and `_clone_existing` call this while adding a slide. The returned ID is printed in the XML snippet the user must add to `presentation.xml`.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for the presentation’s slide list. This ID is separate from the slide filename and from the relationship ID.

**Data flow**: It reads `ppt/presentation.xml` → finds existing `<p:sldId>` numeric IDs → returns one greater than the largest, or 256 if the deck has no listed slide IDs.

**Call relations**: `_create_from_layout` and `_clone_existing` call this after registering the slide relationship. They use the returned number in the printed `<p:sldId>` line for the user.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that is connected to an existing slide layout. A layout is a PowerPoint template for where placeholders and formatting should come from.

**Data flow**: It takes the unpacked folder and a layout filename → verifies that the layout exists → creates a new blank slide XML file → creates a relationship file pointing the slide to the layout → registers the slide’s content type and presentation relationship → prints the XML line the user should add to the slide list.

**Call relations**: `run_add` calls this when the source name looks like `slideLayoutN.xml`. It uses `_next_slide_number`, `_register_content_type`, `_register_presentation_rel`, and `_next_slide_id` to create the slide without colliding with existing package records.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Creates a new slide by copying an existing slide file. It copies the slide’s relationships too, but removes any notes-slide relationship so the duplicate does not point to the original slide’s speaker notes.

**Data flow**: It takes the unpacked folder and a source slide filename → verifies that the source slide exists → chooses a new slide filename → copies the slide XML → copies and edits the relationship file if present → registers the new slide in content types and presentation relationships → prints the XML line the user should add to the slide list.

**Call relations**: `run_add` calls this when the source is not a slide layout. It relies on `_next_slide_number`, `_parse_xml`, `_write_xml`, `_register_content_type`, `_register_presentation_rel`, and `_next_slide_id` to make the duplicate usable by PowerPoint.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Decides which kind of slide-add operation to run: create from a layout or clone an existing slide. It is the main worker behind the `add` command.

**Data flow**: It takes the unpacked folder and a source name → checks whether the source name looks like a slide layout file → calls the layout-creation path or the slide-cloning path → leaves the new files and metadata updates on disk.

**Call relations**: `_cmd_add` calls this after checking that the unpacked folder exists. It then delegates to `_create_from_layout` or `_clone_existing` based on the source argument.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a `.pptx` file and discovers the presentation’s slide order, including which slides are hidden. This lets thumbnail output match what the deck says, not just what files happen to exist.

**Data flow**: It takes a `.pptx` path → opens it as a zip archive → reads the presentation relationship file to map relationship IDs to slide filenames → reads `presentation.xml` to follow the slide list order → returns a list of slide names with hidden flags.

**Call relations**: `run_thumbnail` calls this before rendering images. Its slide-order list is later combined with rendered image files by `_pair_slides_with_images`.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the PowerPoint deck into JPEG slide images. It does this in two stages: LibreOffice converts the deck to PDF, and `pdftoppm` converts the PDF pages to image files.

**Data flow**: It takes a `.pptx` path and a temporary working folder → runs `soffice` in headless mode, meaning without opening a visible app window → checks that a PDF was created → runs `pdftoppm` to make JPEGs → returns the generated image paths in sorted order.

**Call relations**: `run_thumbnail` calls this after reading slide order. The returned images are paired with slide names by `_pair_slides_with_images`.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a simple gray image with an X across it to stand in for a hidden slide. This keeps hidden slides visible in the thumbnail grid as positions in the deck, without pretending to show their real content.

**Data flow**: It takes desired image dimensions → creates a gray blank image → draws two diagonal lines across it → returns the image object.

**Call relations**: `_pair_slides_with_images` calls this whenever the slide-order list says a slide is hidden. The placeholder is then saved and treated like a normal slide image by the grid builder.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (Draw, new).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches the ordered slide list with the rendered image files. It also inserts placeholder images for hidden slides, because rendering tools may skip hidden slides.

**Data flow**: It takes slide-order entries, rendered image paths, and a working folder → uses the first rendered slide size for hidden placeholders, or a default widescreen size if none exist → walks the slide order → pairs visible slides with rendered images and hidden slides with generated placeholders → returns image-and-label pairs.

**Call relations**: `run_thumbnail` calls this after `_extract_slide_order` and `_render_slide_images`. It calls `_make_hidden_placeholder` for hidden slides, then hands the complete ordered set to `_compose_grid`.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one contact-sheet image from slide thumbnails and labels. It is like laying printed slide snapshots on a white board in neat rows and columns.

**Data flow**: It takes image-and-label pairs, a column count, and a thumbnail width → calculates label height, slide aspect ratio, row count, and canvas size → opens each slide image, shrinks it to fit, centers it in its cell, draws its label, and outlines it → returns the completed grid image.

**Call relations**: `run_thumbnail` calls this once for each group of slides that fits in a grid. The returned image is then saved as a JPEG file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (Draw, load_default, new, open).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG thumbnail grids for a PowerPoint file. It is the main worker behind the `thumbnail` command.

**Data flow**: It takes a `.pptx` path, an output prefix, and a column count → reads the slide order → creates a temporary folder → renders slides to images → pairs images with slide labels and hidden placeholders → splits the list into grid-sized chunks → composes and saves each grid image → returns the saved file paths.

**Call relations**: `_cmd_thumbnail` calls this after validating the input file and limiting the column count. It coordinates `_extract_slide_order`, `_render_slide_images`, `_pair_slides_with_images`, and `_compose_grid`, then reports saved grids back to the command wrapper.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `slides.py clean`. It validates the folder path, runs the cleanup, and prints a user-readable summary.

**Data flow**: It takes parsed command-line arguments → turns the folder argument into a path → exits with an error if it does not exist → calls `run_clean` → prints either the deleted file list or a message that nothing was removed.

**Call relations**: The argument parser connects the `clean` subcommand to this function. This function is the bridge between the user’s terminal command and the cleanup logic in `run_clean`.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `slides.py add`. It checks that the unpacked PowerPoint folder exists, then starts the slide-add operation.

**Data flow**: It takes parsed command-line arguments → converts the folder argument to a path → exits with an error if the folder is missing → passes the folder and source name to `run_add`.

**Call relations**: The argument parser connects the `add` subcommand to this function. It does only the command-line validation, while `run_add` decides whether to clone a slide or create one from a layout.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `slides.py thumbnail`. It validates the PowerPoint file, applies the maximum column limit, runs thumbnail creation, and prints the output paths.

**Data flow**: It takes parsed command-line arguments → checks that the input exists and ends in `.pptx` → limits the requested column count to the configured maximum → calls `run_thumbnail` → prints the saved grid files, or exits with an error if thumbnail creation fails.

**Call relations**: The argument parser connects the `thumbnail` subcommand to this function. It wraps `run_thumbnail` with user-facing checks, warnings, success messages, and error reporting.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface for the script. It tells Python which subcommands exist, which arguments they need, and which function should run for each one.

**Data flow**: It creates an argument parser → adds `clean`, `add`, and `thumbnail` subcommands → attaches their required arguments and default callback functions → returns the finished parser.

**Call relations**: When the script is run directly, the bottom of the file calls `build_parser`, parses the user’s command, and then calls the selected command function. This is the front door for all three workflows.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual command-line document unpacking`

A .pptx file is really a ZIP archive, like a bundled folder, containing many XML files that describe slides, layouts, relationships, and other PowerPoint details. This script opens that bundle, extracts everything into a chosen directory, and then tidies the XML files it finds. That matters because raw PowerPoint XML is often hard to read or compare in version control; pretty-printing gives it consistent indentation, like neatly arranging a messy filing cabinet. The main path starts with basic safety checks: the input must exist and must end in .pptx. Then the script creates the output directory, unzips the PowerPoint contents into it, finds both .xml files and .rels relationship files, and runs two cleanup passes. First it tries to reformat each XML file with lxml, an XML parsing library. Second it replaces curly “smart quotes” with explicit XML character entities, which keeps those characters stable in XML text. The cleanup helpers deliberately ignore failures on individual files, so one odd or non-parseable file does not stop the whole unpacking job. When run directly from the command line, it prints either a success message with the number of XML files processed or an error message.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker for unpacking a PowerPoint file into a folder. It checks that the input looks valid, extracts the ZIP contents, cleans up XML-related files, and reports either success or a clear error.

**Data flow**: It receives a path to a .pptx file and a destination folder. It turns those strings into filesystem paths, checks that the source exists and has the right extension, creates the destination folder, extracts the archive there, finds .xml and .rels files, sends each one through formatting and quote-normalizing steps, then returns an ExtractionResult with the number of XML files plus a message. If the source is missing, has the wrong extension, or is not a valid ZIP archive, it returns no result and an error message instead.

**Call relations**: This function is the center of the script. The command-line block calls it after reading the user’s arguments. During the unpacking flow it hands every XML-like file to _prettify_xml first, then to _escape_smart_quotes, and finally packages the count into an ExtractionResult for the caller.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper rewrites one XML file with consistent indentation so it is easier for people and tools to inspect. It is a cleanup step, not the part that extracts the PowerPoint archive.

**Data flow**: It receives a filesystem path. It tries to parse the file as XML, asks the XML library to indent the document using two spaces, converts it back to UTF-8 bytes with an XML declaration, and writes those bytes over the original file. If parsing or writing fails, it quietly leaves the file as it was.

**Call relations**: extract_pptx calls this helper once for each .xml and .rels file it found after extraction. After this formatting pass is complete, extract_pptx moves on to _escape_smart_quotes for the same group of files.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks with XML entity text, such as &#x201C;, so those characters are represented explicitly in the XML. This helps avoid surprises when XML is edited, compared, or processed by different tools.

**Data flow**: It receives a filesystem path, reads the file as UTF-8 text, and checks whether it contains curly single or double quotes. If none are present, it does nothing. If they are present, it replaces each one using a fixed lookup table and writes the changed text back to the same file. If reading or writing fails, it quietly leaves the file unchanged.

**Call relations**: extract_pptx calls this after the pretty-printing pass for every XML-related file. It is the final cleanup step before extract_pptx reports how many files were processed.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).
