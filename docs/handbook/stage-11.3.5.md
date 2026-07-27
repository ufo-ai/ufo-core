# PDF form filling, layout inspection, and rendering tools  `stage-11.3.5`

This stage is a set of PDF command-line tools used when the system needs to understand, prepare, or display PDF documents. It is not the main work loop itself. It is shared support for document tasks, like a small toolbox used before or during form processing.

The form filling tool works with PDFs that already contain fillable fields. It can find those fields, describe them in JSON, which is a simple text format for structured data, and later fill the PDF using values from JSON.

The layout tool is for static PDFs that do not have built-in form fields. It inspects where things appear on the page, previews where new fill areas should go, and can place text onto the PDF as annotations. In effect, it helps mark up a plain paper-like document so the system can fill it reliably.

The render tool converts PDF pages into PNG image files. These images can be used for viewing, checking layout, or image-based processing. Together, the tools let the system inspect, fill, mark up, and visually render PDFs.

## Files in this stage

### PDF Form Operations
Detects, exports, and fills interactive PDF form fields using JSON field data.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command invocation`

Fillable PDFs store their boxes, checkmarks, drop-downs, and radio buttons in a special PDF structure called an AcroForm. This file gives the project a native way to inspect and fill those fields instead of guessing where text should go on the page. Without it, users would have to place text manually or risk writing values into the wrong spots.

The file works like a translator between PDFs and simple JSON. First, it opens a PDF with pypdf, a Python library for reading and writing PDF files. It looks for normal AcroForm fields, and if those are missing, it also checks for “widget” annotations, which are the visible form controls on PDF pages. It turns each discovered field into a simple Python object: text fields, checkboxes, radio groups, or choice lists.

For extraction, it records each field’s name, type, page number, screen-friendly rectangle, and allowed values where needed. For filling, it reads a JSON list of requested values, checks that the names, pages, and allowed options are valid, then writes the values into a cloned PDF. A notable detail is coordinate conversion: PDFs measure positions from the bottom of the page, while many layout tools think from the top, so this file flips rectangles to make the output easier to use.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has visible fillable controls that are not reported through the usual form-field list. This matters because some PDFs are built loosely, and a simple field lookup would miss them.

**Data flow**: It receives an opened PDF reader → scans every page’s annotations for widget entries that also declare a field type → returns true as soon as it finds one, or false if none are found.

**Call relations**: The detect command calls this after asking pypdf for normal fields. If normal fields are absent but orphaned widgets exist, detection can still correctly say the PDF is fillable.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a PDF field by walking from a widget annotation up through its parent field objects. This is needed because PDFs can store a field name in pieces, like folders in a path.

**Data flow**: It receives one annotation dictionary → collects each `/T` name it finds while moving through parent links → returns a dotted name such as `parent.child`, or nothing if no name parts exist.

**Call relations**: The AcroForm extraction path uses this while scanning page annotations. It lets the extractor match visible widgets back to the field metadata returned by the PDF reader.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns a raw PDF field dictionary into one of this file’s simpler field objects. It hides PDF-specific codes like `/Tx`, `/Btn`, and `/Ch` behind plain kinds such as text, checkbox, and choice.

**Data flow**: It receives raw PDF metadata and a field name → looks at the PDF field type code → creates the matching field object, delegating checkbox and choice details to helper functions → returns a FormField-style object.

**Call relations**: Both AcroForm extraction and widget fallback extraction call this whenever they discover a field. It hands checkbox fields to `_build_checkbox` and choice fields to `_build_choice` so each special type can collect its own allowed values.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to discover which PDF value means checked and which means unchecked. This is important because checked boxes are not always stored as a simple true or false.

**Data flow**: It receives raw checkbox metadata and a name → reads the available states if the PDF exposes them → chooses an on value and an off value, warning if the states look unusual → returns a CheckboxField.

**Call relations**: _build_field_from_dict` calls this for button fields that should be represented as checkboxes. The resulting object later helps extraction show valid values and filling reject invalid checkbox input.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a choice field, such as a drop-down list. It records the values the PDF accepts and the human-readable text that may be shown to the user.

**Data flow**: It receives raw choice-field metadata and a name → loops through the PDF’s listed states/options → normalizes each option into a value/text pair → returns a ChoiceField.

**Call relations**: _build_field_from_dict` calls this when it sees a PDF choice field. The extracted choices are later written to JSON and used to validate fill values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a checkbox’s checked value when the earlier metadata did not provide it. It looks at the checkbox’s appearance table, which is how the PDF describes the visual checked and unchecked states.

**Data flow**: It receives a resolved widget annotation and a CheckboxField object → if the checkbox already has an on value, it does nothing → otherwise it looks for appearance keys other than `/Off` and updates the checkbox object in place.

**Call relations**: The widget fallback extractor calls this after building a checkbox from an annotation. It improves checkbox metadata before the field is returned to the caller.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from bottom-origin coordinates to top-origin coordinates. In plain terms, it changes “distance from the bottom of the page” into “distance from the top,” which is easier for many layout tools and people to read.

**Data flow**: It receives a rectangle and the page height → converts the rectangle numbers to floats → subtracts the vertical coordinates from the page height → returns the flipped rectangle.

**Call relations**: Field extraction calls this whenever it records where a field sits on a page. Radio option collection also uses it so all exported positions follow the same coordinate style.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts form fields directly from page widget annotations when the PDF does not provide a normal AcroForm field list. This is the fallback path for PDFs whose form data is present but poorly connected.

**Data flow**: It receives an opened PDF reader → walks through each page and each annotation → keeps only widget annotations with field types and names → builds field objects, sets page and rectangle information, improves checkbox values when possible → returns a list of fields.

**Call relations**: _extract_from_acroform` calls this when `reader.get_fields()` finds no normal fields. It relies on `_build_field_from_dict`, `_flip_rect`, and `_extract_checkbox_on_value` to turn raw annotations into useful field descriptions.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the fillable-field map from a PDF using the standard AcroForm structure, with extra work to locate fields on pages and group radio-button options. This is the main discovery routine used before exporting or filling.

**Data flow**: It receives an opened PDF reader → asks pypdf for fields → falls back to widget extraction if none are found → builds field objects, tracks possible radio groups, scans page annotations to attach page numbers and rectangles → returns fields sorted in reading order.

**Call relations**: The extract and fill commands both call this to understand the PDF before doing their work. It coordinates helpers for field creation, full-name lookup, rectangle flipping, widget fallback, and radio option collection.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one visible radio-button option to a radio group. Radio buttons are special because several separate circles belong to one named question, and each circle has its own value and location.

**Data flow**: It receives one annotation, the group name, page information, page height, and the shared radio-group dictionary → finds the single non-off appearance value → creates the group if needed → stores that option’s value and flipped rectangle.

**Call relations**: _extract_from_acroform` calls this while scanning annotations for fields that were identified as radio groups. It uses `_flip_rect` so radio option positions match the rest of the exported field data.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a stable reading-order sort for extracted fields. It groups nearby vertical positions into rows, then orders fields by page, row, and left-to-right position.

**Data flow**: It receives a field object → chooses the field rectangle, or the first radio option rectangle for a radio group → turns the vertical position into a coarse row number → returns a tuple used for sorting.

**Call relations**: The AcroForm extractor uses this when ordering the final field list. That makes the JSON output easier for a person to scan because fields appear roughly as they do on the PDF page.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts one internal field object into a plain dictionary that can be written as JSON. This is the bridge from Python objects to a simple file format other tools can read.

**Data flow**: It receives a FormField, CheckboxField, RadioGroup, or ChoiceField → copies common details like name, kind, page, and rectangle → adds type-specific details such as checkbox values, radio options, or choices → returns a JSON-ready dictionary.

**Call relations**: The extract command calls this for each discovered field before writing the output JSON. It is the final cleanup step after field discovery.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a proposed value is allowed for a field. It prevents writing impossible checkbox states, unknown radio options, or invalid drop-down choices into the PDF.

**Data flow**: It receives a field description and a requested string value → compares the value against that field’s allowed values when the field has restrictions → returns an error message if invalid, or nothing if acceptable.

**Call relations**: _validate_fill_entries` calls this while checking the user’s fill JSON. If it returns an error, the fill command stops before writing a bad output PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the `detect` command, which tells the user whether a PDF appears to contain fillable fields. It is a quick yes-or-no check before trying extraction or filling.

**Data flow**: It receives command arguments → verifies that exactly one PDF path was supplied → opens the PDF → checks normal fields and orphaned widgets → prints either a fillable-fields message or a suggestion to use manual layout tools.

**Call relations**: The main command dispatcher calls this when the user chooses `detect`. It uses `_has_orphaned_widgets` as a backup check when pypdf’s normal field list is empty.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the `extract` command, which writes a JSON description of all fillable fields in a PDF. Users can inspect or edit this JSON to prepare values for filling.

**Data flow**: It receives command arguments → verifies input PDF and output JSON paths → opens the PDF → extracts fields → converts each field to a dictionary → creates the output folder if needed → writes formatted JSON and prints a count.

**Call relations**: The main dispatcher calls this for `extract`. It depends on `_extract_from_acroform` for discovery and `_field_to_dict` for turning field objects into JSON-ready records.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the `fill` command, which writes user-provided values into a fillable PDF and saves a new PDF. It is the file’s main “make the completed form” operation.

**Data flow**: It receives command arguments → reads the values JSON → opens and analyzes the input PDF → validates field names, pages, and allowed values → groups values by page → clones the PDF and updates each page’s form fields → writes the finished PDF.

**Call relations**: The main dispatcher calls this for `fill`. It uses `_extract_from_acroform` to learn what fields exist, `_validate_fill_entries` to stop bad input early, and pypdf’s writer to apply the accepted values.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks an entire list of requested fill entries before the PDF is modified. It catches wrong field names, wrong page numbers, and invalid values in one pass.

**Data flow**: It receives the user’s list of value entries and a lookup table of known fields → checks each entry against the PDF metadata → prints clear error messages for problems → returns true if any error was found, otherwise false.

**Call relations**: The fill command calls this after extracting field metadata and before creating the output PDF. For value-specific checks, it hands each proposed value to `_validate_fill_value`.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Acts as the command-line entry point for the script. It chooses which command to run based on the first word after the script name.

**Data flow**: It reads command-line arguments from `sys.argv` → checks that a known subcommand was provided → prints usage and exits on bad input → otherwise calls the selected command with the remaining arguments.

**Call relations**: Python calls this when the file is run directly. It dispatches to `cmd_detect`, `cmd_extract`, or `cmd_fill` through the `SUBCOMMANDS` table.

*Call graph*: 1 external calls (exit).


### Static Layout Annotation
Inspects non-fillable PDF layouts, previews field placement, and writes annotation text onto static documents.

### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `on-demand command-line PDF extraction, preview, or filling`

Some PDFs look like forms but do not contain real form fields. They are more like a printed sheet: the boxes and lines are visible, but a program cannot simply ask the PDF where to type. This file solves that problem in three steps. First, it can scan a PDF and record useful landmarks such as words, long horizontal lines, small square boxes that look like checkboxes, and row-like spaces between lines. Second, it can draw colored rectangles on an image preview so a human can check that planned fill areas line up with the document. Third, it can place text onto the PDF using PDF annotations, which are added pieces of text laid over the page.

A key complication is coordinates. Images usually measure from the top-left corner, while PDFs often place annotations using a bottom-left origin. `CoordMapper` acts like a translator between those map systems so text lands in the right place. Before writing the final PDF, the fill step also checks for common mistakes, such as text boxes that are too short for the chosen font or fields that overlap. Without this file, filling a flat PDF would be mostly guesswork: the system would lack a way to find layout clues, preview placements, and safely add text to the finished document.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: This method converts a rectangle from the coordinate system used in the field description into the coordinate system needed for a PDF annotation. Someone uses it so that a box chosen on an image or extracted PDF page becomes a placement rectangle that the PDF writer understands.

**Data flow**: It takes a rectangle described as four numbers, plus the mapper’s stored page sizes and coordinate-system choice. If the input came from an image, it scales the rectangle to the PDF page size and flips the vertical direction; otherwise it only flips the vertical direction for PDF annotation use. It returns a new four-number rectangle ready to pass to the PDF annotation library.

**Call relations**: During the fill command, `_validate_and_fill` creates a `CoordMapper` for the page being written and asks this method to translate each field’s content area. The translated rectangle is then handed to the `FreeText` annotation so the text appears at the intended spot.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: This function scans one PDF page and collects the visual clues that matter for building a fill plan. It records the page size, words, long horizontal rules, and small square boxes that likely represent checkboxes.

**Data flow**: It receives a PDF page object and its page number. It reads the page’s drawing objects and words, filters lines to keep only long horizontal ones, filters rectangles to keep small near-square ones, rounds positions for cleaner output, and stores everything in a `PageLayout` object. The result is a structured summary of that single page.

**Call relations**: `_extract_all_pages` calls this once for each page while scanning a PDF. After `_extract_page` returns the page summary, `_extract_all_pages` asks `_compute_row_ranges` to add row spacing information before saving the page layout for output.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: This function turns detected horizontal lines into row bands, which are the spaces between one line and the next. This is useful for forms that are arranged like tables or ruled sections.

**Data flow**: It reads the horizontal rule positions already stored in a `PageLayout`. It sorts their vertical positions, pairs each line with the next one, and appends a row range containing the top, bottom, and height of that space. It changes the given `PageLayout` in place and does not return a separate value.

**Call relations**: `_extract_all_pages` calls this immediately after `_extract_page` has found the lines on a page. Its output becomes part of the layout data that `cmd_extract` writes to JSON.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: This function scans an entire PDF and produces a layout summary for every page. It is the main worker behind the extract command.

**Data flow**: It receives the path to a PDF file. It opens the PDF, loops through every page, extracts that page’s layout, computes row ranges, and gathers all page summaries into a list. It returns that list of `PageLayout` objects.

**Call relations**: `cmd_extract` calls this after checking the command-line arguments. Inside the loop, it delegates page-level scanning to `_extract_page` and row calculation to `_compute_row_ranges`, then hands the finished list back to `cmd_extract` for JSON conversion.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: This function converts the page layout objects into plain dictionaries that can be written as JSON. It bridges the program’s internal data shape and a file format that other tools or humans can read.

**Data flow**: It receives a list of `PageLayout` objects. For each page, it copies the page number, size, text elements, horizontal rules, checkbox candidates, and row ranges into a plain dictionary. It returns a list of those dictionaries.

**Call relations**: `cmd_extract` calls this after `_extract_all_pages` has finished scanning the PDF. The returned dictionaries are then passed to JSON writing so the extracted layout becomes an output file.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: This function reads a field plan, checks it for likely placement mistakes, and writes the requested text onto a PDF. It is the core of the fill command.

**Data flow**: It receives paths for the input PDF, the JSON field description, and the output PDF. It reads the JSON, opens the PDF, records each page’s size, then walks through every planned form field. For each field with text, it checks whether the content box is tall enough, checks whether it overlaps earlier placed content or label boxes, converts the field rectangle into PDF annotation coordinates, creates a text annotation, and adds it to the correct page. If validation errors are found, it prints them and exits without writing a successful output; otherwise it creates the output folder if needed and writes the filled PDF.

**Call relations**: `cmd_fill` calls this after confirming the user supplied the right number of command-line arguments. It uses `_rects_overlap` to catch collisions between boxes and `CoordMapper.to_annotation_rect` to translate field coordinates before creating `FreeText` annotations for the PDF writer.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: This small helper answers one question: do two rectangles touch or cover the same space? It is used to prevent fields from being placed on top of each other.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges to see whether one is completely to the left, right, above, or below the other. It returns `True` if they overlap and `False` if they do not.

**Call relations**: `_validate_and_fill` calls this while checking each new field against rectangles that have already been placed on the same page. If it reports an overlap, `_validate_and_fill` records a human-readable error instead of silently producing a bad PDF.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: This is the command handler for `layout.py extract`. It scans a PDF and writes a JSON file describing the page layout.

**Data flow**: It receives the command-line arguments after the word `extract`. If the user did not provide exactly an input PDF and output JSON path, it prints usage text and exits. Otherwise it scans the PDF, converts the page layouts to dictionaries, writes them as formatted JSON, counts the extracted items, and prints a short summary.

**Call relations**: `main` dispatches to this function when the first command-line word is `extract`. It relies on `_extract_all_pages` for the actual PDF scan and `_pages_to_dict` for preparing the result for JSON output.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: This is the command handler for `layout.py preview`. It draws the planned field boxes onto an image so a person can visually confirm that the boxes line up with the document.

**Data flow**: It receives the page number, field JSON path, input image path, and output image path from the command line. It reads the field plan, opens the image, draws red rectangles around content areas and blue rectangles around label boxes for the requested page, saves the marked-up image, and prints how many fields were highlighted.

**Call relations**: `main` dispatches to this function when the user chooses `preview`. Unlike the fill path, this function does not alter a PDF; it uses the same field descriptions to create a visual check before someone runs `cmd_fill`.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: This is the command handler for `layout.py fill`. It checks the command-line input and starts the process of writing planned text onto a PDF.

**Data flow**: It receives the command-line arguments after the word `fill`. If the user did not provide an input PDF, field JSON, and output PDF path, it prints usage text and exits. If the arguments are correct, it passes those three paths to `_validate_and_fill`.

**Call relations**: `main` dispatches to this function when the first command-line word is `fill`. It is intentionally thin: the detailed checking, coordinate conversion, annotation creation, and PDF writing all happen inside `_validate_and_fill`.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: This is the entry point when the file is run as a script. It chooses which command to run based on the first command-line argument.

**Data flow**: It reads `sys.argv`, which is the list of words typed in the command line. If there is no recognized subcommand, it prints the available choices and exits. If the subcommand is valid, it calls the matching command function with the remaining arguments.

**Call relations**: When someone runs `python layout.py ...`, the bottom of the file calls `main`. From there, control is handed to `cmd_extract`, `cmd_preview`, or `cmd_fill`, which each run one user-facing workflow.

*Call graph*: 1 external calls (exit).


### Page Rasterization
Renders PDF pages into PNG images for viewing, inspection, or image-based processing.

### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `on-demand command-line run`

This is a small command-line tool for converting a PDF into separate image files, one image per page. Without it, any part of the project that expects PDF pages as PNG images would need to repeat this conversion work itself.

The file works like a photocopier that scans a multi-page document and saves each page as its own picture. It takes two pieces of information: the path to the input PDF and the folder where the images should be written. It creates the output folder if it does not already exist, then asks the pdf2image library to render the PDF pages at 200 DPI. DPI means “dots per inch”; here it controls how detailed the rendered page images are.

After each page is rendered, the file checks whether the image is larger than 1000 pixels in either direction. If so, it shrinks the image while keeping the same shape, so pages do not become too large to store or process comfortably. Each page is saved with a predictable name like page_1.png, page_2.png, and so on. The script prints progress messages as it saves pages, then prints a final count.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts a PDF file into PNG images, one image per page, and writes them into a chosen output folder. It also limits very large page images to a maximum size so the results stay manageable.

**Data flow**: It receives a PDF path and an output directory path. It creates the output directory if needed, reads the PDF through pdf2image, optionally resizes each rendered page image, saves each page as page_N.png, and prints progress. The result is a folder full of PNG page images; it does not return a value.

**Call relations**: This is the worker function used by main after the command-line arguments have been checked. It relies on pathlib.Path to create and build file paths, and on pdf2image.convert_from_path to do the actual PDF-to-image rendering.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Acts as the command-line doorway into the tool. It checks that the user supplied exactly the two required arguments: the input PDF and the output folder.

**Data flow**: It reads the command-line arguments from sys.argv. If the user gave the wrong number of arguments, it prints a usage message and exits with an error code. If the arguments are valid, it passes them to render, which performs the conversion.

**Call relations**: This function runs when the file is executed directly as a script. Its job is to do the small amount of command-line setup, then hand off the real PDF rendering work to render; when the arguments are invalid, it uses sys.exit to stop the program clearly.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
