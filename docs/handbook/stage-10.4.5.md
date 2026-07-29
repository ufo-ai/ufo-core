# PDF form, layout, and rendering tools  `stage-10.4.5`

This stage is shared behind-the-scenes support for working with PDFs. It is not the main application loop. Instead, it provides small command-line tools that other workflows can call when they need to inspect, fill, or preview documents.

The formfill tool works with PDFs that already contain real form fields, like text boxes built into the file. It can check whether those fields exist, list them in JSON, which is a simple structured text format, and then fill the PDF using values from that JSON.

The layout tool is for PDFs that look like forms but do not have real fillable fields. It helps inspect the page, preview where answers should appear, and place text at chosen coordinates, like putting labels onto a printed form.

The render tool turns each PDF page into a PNG image. These page images can be shown to a user, checked visually, or passed to other tools. Together, the three tools cover real forms, static forms, and visual inspection.

## Files in this stage

### Fillable Form Fields
Detects, extracts, and fills real PDF form fields using JSON data.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command invocation`

This file solves a practical PDF problem: some PDFs contain built-in form fields, like text boxes, checkboxes, radio buttons, and drop-down choices. If those fields exist, it is much better to fill them directly than to guess where text should be drawn on the page. Without this file, the system would have to treat every PDF like a flat picture, which is less reliable and harder to edit.

The file uses pypdf, a Python library for reading and writing PDFs. It first looks for form metadata called an AcroForm, which is the standard place PDFs store fillable fields. If that is missing or incomplete, it can also inspect page annotations directly. An annotation is an item attached to a PDF page; a widget annotation is the visible part of a form field.

The file turns raw PDF field data into simple Python objects such as text fields, checkboxes, radio groups, and choice fields. It records useful details like the field name, page number, screen rectangle, and valid values. Rectangles are converted into a top-left-style coordinate system that is easier for layout tools to understand.

The three user-facing commands are detect, extract, and fill. Detect says whether native fields exist. Extract writes a JSON map of fields. Fill validates the provided values before writing a new completed PDF.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has visible form widgets even when the normal form directory is missing. This helps detect poorly structured PDFs that still contain usable fillable fields.

**Data flow**: It receives a PDF reader, looks through each page, then checks each page annotation for a widget with a field type. If it finds one, it returns true; if it reaches the end without finding one, it returns false. It does not change the PDF.

**Call relations**: The detect command calls this after checking the normal form fields. It acts as a backup check so the tool does not wrongly say a PDF has no fillable fields just because its form information is stored in an unusual way.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a PDF field from an annotation and its parent fields. This matters because nested PDF fields can have names made from several parts, like a folder path.

**Data flow**: It receives one annotation, walks upward through its parent chain, collects each name part, reverses them into parent-to-child order, and joins them with dots. It returns the full name string, or nothing if no name parts are found.

**Call relations**: Field extraction from the AcroForm uses this while scanning page annotations. The full name lets that extractor match a visible widget on a page back to the field metadata found earlier.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field metadata into one of this file's simpler field objects. It hides PDF-specific codes behind plain categories such as text, checkbox, choice, or unknown.

**Data flow**: It receives a raw PDF field dictionary and a field name. It reads the PDF field type code, then creates the matching field object; button fields are handed to the checkbox builder, and choice fields are handed to the choice builder. It returns the newly created field description.

**Call relations**: Both extraction paths call this when they discover a field. It delegates special cases to _build_checkbox and _build_choice so the main extractors can work with simple FormField-style objects instead of raw PDF dictionaries.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which stored value means checked and which means unchecked. This is important because PDFs do not always use the same label for the checked state.

**Data flow**: It receives raw checkbox metadata and a name. It looks at the available state values, chooses an on value and an off value when possible, prints a warning for unusual two-state checkboxes, and returns a CheckboxField object.

**Call relations**: _build_field_from_dict calls this when it sees a PDF button field. The resulting checkbox metadata is later used during extraction output and during fill validation.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a drop-down or list-style PDF choice field. It records both the internal value and the human-readable text when the PDF provides both.

**Data flow**: It receives raw choice-field metadata and a name. It loops through the field's listed states or options, normalizes each one into a small value/text dictionary, and returns a ChoiceField object containing those choices.

**Call relations**: _build_field_from_dict calls this for PDF choice fields. The extracted choices are later written to JSON and used to reject invalid fill values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Finds a checkbox's checked value from its appearance data when that value was not already known. Appearance data describes how a PDF field should look in each state.

**Data flow**: It receives a resolved PDF annotation and a CheckboxField object. If the checkbox already has an on value, it does nothing. Otherwise it reads the normal appearance state names, chooses the first one that is not /Off, and updates the checkbox object in place.

**Call relations**: _extract_from_widgets calls this for checkbox widgets found directly on pages. It fills in missing checkbox details before those fields are returned to the higher-level extractor.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle into a coordinate style that is easier to read for page layout work. PDFs usually measure upward from the bottom of the page, while many layout systems measure downward from the top.

**Data flow**: It receives a rectangle and the page height. It converts the rectangle values to numbers, flips the vertical coordinates using the page height, and returns the transformed rectangle as left, top, right, bottom.

**Call relations**: The widget extractor, AcroForm extractor, and radio-option collector call this whenever they record where a field appears on the page. It gives all extracted locations the same coordinate meaning.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts form fields by scanning visible widget annotations directly on each PDF page. This is the fallback path for PDFs whose normal form index is missing or not useful.

**Data flow**: It receives a PDF reader, walks through every page and annotation, keeps only widget annotations that have a field type and name, builds a field object for each one, records its page and rectangle, improves checkbox metadata when needed, and returns the list of fields.

**Call relations**: _extract_from_acroform calls this when the PDF reader cannot find normal form fields. Inside that fallback path, it relies on _build_field_from_dict, _flip_rect, and _extract_checkbox_on_value to create complete field descriptions.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the fillable fields from a PDF using the standard AcroForm structure, with special handling for radio groups and a fallback for unusual PDFs. This is the main field-discovery routine used by the tool.

**Data flow**: It receives a PDF reader and asks pypdf for the raw fields. If none are found, it falls back to scanning widgets directly. Otherwise it builds simple field objects, tracks possible radio groups, scans pages to attach page numbers and rectangles, collects radio button options, skips fields that cannot be found on a page, sorts the result, and returns the final field list.

**Call relations**: The extract command uses this to produce JSON field metadata, and the fill command uses it to know what fields and values are valid. It coordinates several helpers: name reconstruction, field building, rectangle conversion, fallback widget extraction, and radio-option collection.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one visible radio-button option to a radio group description. A radio group is a set of buttons where only one choice should be selected.

**Data flow**: It receives an annotation, the group name, the page number information, page height, and the radio-groups dictionary being built. It reads the annotation's non-off appearance value, creates the group if needed, flips the option rectangle, and appends a value-and-location entry to that group.

**Call relations**: _extract_from_acroform calls this while scanning page annotations that belong to radio-button parent fields. It builds the list of valid radio choices that later appears in extracted JSON and fill validation.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Creates a sorting key so extracted fields appear in a natural page order. This makes the JSON easier for a person or another tool to read.

**Data flow**: It receives a field. For radio groups it uses the first option rectangle; for other fields it uses the field rectangle. It groups vertical positions into rough rows and returns page number, row, and left position as the sort key.

**Call relations**: _extract_from_acroform uses this when ordering the combined list of normal fields and radio groups. The result is a field list that is closer to how someone would scan the form visually.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts an internal field object into a JSON-friendly dictionary. This is what lets extracted PDF fields be saved as a simple file that other tools or people can inspect.

**Data flow**: It receives a FormField or one of its specialized forms. It writes common data such as name, kind, page, and rectangle, then adds checkbox values, radio options, or choice options when relevant. It returns a plain dictionary.

**Call relations**: The extract command calls this for every discovered field before writing the JSON file. It is the bridge between this file's Python objects and the portable JSON output.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a proposed value is allowed for a checkbox, radio group, or choice field. This prevents writing invalid values into the PDF when the valid options are known.

**Data flow**: It receives a field description and a proposed string value. Depending on the field type, it compares the value to the allowed checkbox states, radio option values, or choice values. It returns an error message if the value is invalid, or nothing if it is acceptable.

**Call relations**: _validate_fill_entries calls this for entries that include a value. It supplies the field-specific part of fill validation before cmd_fill writes the final PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the detect command, which tells the user whether a PDF appears to contain native fillable fields. It is a quick yes-or-no check before trying extraction or filling.

**Data flow**: It receives command-line arguments, expects exactly one PDF path, opens that PDF, checks for normal fields and orphaned widgets, then prints a message. If the arguments are wrong, it prints usage text and exits with an error.

**Call relations**: main calls this when the user runs the detect subcommand. It uses _has_orphaned_widgets as a backup after pypdf's normal field check, then reports whether this file's native form workflow is likely usable.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract command, which writes a JSON description of the PDF's fillable fields. This gives users or later automation a clear template of what can be filled.

**Data flow**: It receives command-line arguments, expects an input PDF and an output JSON path, opens the PDF, extracts the fields, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written.

**Call relations**: main calls this when the user runs the extract subcommand. It relies on _extract_from_acroform for discovery and _field_to_dict for JSON-ready output.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill command, which writes user-provided values into a fillable PDF and saves a new PDF. It validates the requested fields first so mistakes are caught before output is written.

**Data flow**: It receives command-line arguments, expects an input PDF, a values JSON file, and an output PDF path. It reads the values, extracts field metadata from the PDF, validates names, pages, and allowed values, groups values by page, clones the PDF into a writer, updates each page's form fields, creates the output folder if needed, writes the new PDF, and prints a summary.

**Call relations**: main calls this when the user runs the fill subcommand. It uses _extract_from_acroform to understand the PDF and _validate_fill_entries to stop invalid input before handing page-specific values to pypdf's writer.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks a whole list of requested fill entries against the fields actually found in the PDF. It catches wrong field names, wrong page numbers, and invalid field values.

**Data flow**: It receives the user-provided entries and a lookup table of known fields by name. For each entry, it finds the matching field, compares the page if one was supplied, validates the value if present, prints any errors, and returns true if any problem was found.

**Call relations**: cmd_fill calls this before writing to the PDF. For field-type-specific value checks, it delegates to _validate_fill_value, then reports the combined pass-or-fail result back to cmd_fill.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Chooses which command to run based on the first command-line word. It is the script's front door when someone runs formfill.py directly.

**Data flow**: It reads the process command-line arguments, checks that a known subcommand was provided, prints general usage and exits if not, and otherwise calls the selected command with the remaining arguments. It does not return a meaningful value.

**Call relations**: The Python runtime calls main when this file is executed as a script. main dispatches to cmd_detect, cmd_extract, or cmd_fill through the subcommand table.

*Call graph*: 1 external calls (exit).


### Coordinate Text Layout
Inspects static PDFs, previews answer placement, and writes text annotations at chosen coordinates.

### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `manual CLI use`

Some PDFs look like forms but do not contain real fillable fields. This file solves that problem by treating the PDF like a page image: it finds useful landmarks, lets a person define answer boxes in JSON, previews those boxes on an image, and finally writes the answers back into a PDF as annotations. An annotation is extra content placed on top of a PDF page, like putting a sticky note or typed label over a printed form.

The file has three command-line actions. `extract` scans each page and records text, long horizontal lines, small square boxes that look like checkboxes, and row bands between horizontal lines. This gives humans or other tools a map of the page. `preview` draws the planned field boxes on an image, using red for content areas and blue for label boxes, so mistakes are visible before editing the PDF. `fill` reads the field definitions, checks for obvious layout problems such as overlapping boxes or boxes too short for the font, converts coordinates into PDF annotation coordinates, and writes the filled output PDF.

A small `CoordMapper` class does an important translation job. Image coordinates usually count downward from the top-left corner, while PDF coordinates count upward from the bottom-left corner. Without this conversion, text would appear in the wrong place.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: Converts a rectangle from the coordinate system used in the field JSON into the coordinate system expected by PDF annotations. This matters because image-based coordinates and PDF coordinates measure vertical position in opposite directions.

**Data flow**: It receives a box as four numbers, plus the mapper’s stored page sizes and coordinate-system setting. If the box came from an image, it first scales the box from image size to PDF page size, then flips the vertical coordinates. If the box is already in PDF-sized coordinates, it only flips the vertical direction. It returns a four-number rectangle ready to give to the PDF annotation library.

**Call relations**: During the fill step, the PDF-writing code creates a `CoordMapper` for the target page and asks this method to turn each planned content box into an annotation rectangle. The converted rectangle is then handed to the annotation object so the text lands in the intended spot.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: Reads one PDF page and builds a simple layout summary for it. The summary includes page size, visible words, long horizontal rules, and small square boxes that may be checkboxes.

**Data flow**: It takes a page object from `pdfplumber` and a page number. It creates a `PageLayout`, scans the page’s drawn lines for long horizontal separators, scans rectangles for checkbox-like shapes, and asks the page to extract its words. It rounds positions to one decimal place and returns the filled `PageLayout` object.

**Call relations**: `_extract_all_pages` calls this once for each page in the PDF. This function does the close-up page inspection, while `_extract_all_pages` takes care of opening the document and collecting all page results.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: Finds the vertical bands between horizontal rules on a page. These bands are useful when a form is laid out like a table or repeated rows.

**Data flow**: It reads the horizontal-rule positions already stored in a `PageLayout`. It sorts their vertical positions, then records each gap between neighboring rules as a row range with a top, bottom, and height. It changes the given `PageLayout` in place and does not return a separate value.

**Call relations**: `_extract_all_pages` calls this after `_extract_page` has found the horizontal rules. In the extraction flow, page scanning happens first, then row ranges are added as a second pass.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: Opens a whole PDF and extracts layout information from every page. It is the document-level version of the single-page extraction logic.

**Data flow**: It receives a PDF file path. It opens the PDF with `pdfplumber`, loops through the pages in order, extracts each page’s layout, computes row ranges for that page, and adds the result to a list. It returns the list of `PageLayout` objects.

**Call relations**: `cmd_extract` calls this when the user runs the `extract` command. It delegates the detailed work to `_extract_page` and `_compute_row_ranges`, then hands the collected page layouts back to the command so they can be written as JSON.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: Turns internal `PageLayout` objects into plain dictionaries that can be saved as JSON. This makes the extraction output easy for people and other tools to read.

**Data flow**: It receives a list of page layout objects. For each page, it copies the page number, dimensions, text elements, horizontal rules, checkbox candidates, and row ranges into a dictionary. It returns a list of those dictionaries.

**Call relations**: `cmd_extract` calls this after extracting the PDF layout. The command then serializes the returned dictionaries to a JSON file.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: Checks a field-definition JSON file and writes the requested text into a PDF as annotations. It protects against common layout mistakes before producing the final filled PDF.

**Data flow**: It receives paths for the input PDF, the JSON field definitions, and the output PDF. It reads the JSON, opens the PDF, records each page’s size, then walks through the requested form fields. For each field with text, it checks whether the box is tall enough for the font and whether it overlaps earlier placed boxes. If errors are found, it prints them and stops. If the definitions pass, it converts the field’s rectangle to PDF coordinates, creates a free-text annotation, adds it to the correct page, and writes the output file.

**Call relations**: `cmd_fill` calls this when the user runs the `fill` command. Inside the fill flow, it uses `_rects_overlap` to catch collisions, creates a `CoordMapper` to translate positions, and creates `FreeText` annotations through the PDF library before saving the finished document.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: Answers a simple geometry question: do two rectangular boxes touch or cover the same space? It is used to warn about fields that would be drawn on top of each other.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom coordinates. It compares their edges. If one rectangle is completely to the left, right, above, or below the other, it returns `false`; otherwise it returns `true` because the boxes overlap.

**Call relations**: `_validate_and_fill` calls this while checking each new field against boxes that have already been placed. Its result decides whether an overlap error is added before the PDF is written.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the `extract` command-line action. It scans a PDF and writes a JSON layout report.

**Data flow**: It receives the command arguments after the word `extract`. If the user did not provide an input PDF and output JSON path, it prints usage help and exits. Otherwise it scans the PDF, converts the page layouts to JSON-friendly dictionaries, creates the output folder if needed, writes the JSON file, and prints a short summary of what it found.

**Call relations**: `main` dispatches to this function when the first command-line word is `extract`. This function calls `_extract_all_pages` for the real PDF inspection and `_pages_to_dict` to prepare the result for saving.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: Implements the `preview` command-line action. It draws the planned form-field boxes on top of an image so a person can visually check placement before filling the PDF.

**Data flow**: It receives the page number, field JSON path, input image path, and output image path. It loads the JSON and image, draws red rectangles around content areas for the chosen page, draws blue rectangles around label boxes when present, saves the marked-up image, and prints how many fields were highlighted. If the arguments are wrong, it prints usage help and exits.

**Call relations**: `main` dispatches to this function when the user chooses `preview`. Unlike the fill path, this does not edit a PDF; it uses the image library to create a visual check of the same field definitions that `cmd_fill` will later use.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the `fill` command-line action. It is the small command wrapper around the PDF validation and writing process.

**Data flow**: It receives the command arguments after the word `fill`. If the user did not provide input PDF, fields JSON, and output PDF paths, it prints usage help and exits. Otherwise it passes those paths to `_validate_and_fill`, which performs the checks and writes the output.

**Call relations**: `main` dispatches to this function when the first command-line word is `fill`. This function keeps argument checking separate from the larger filling routine in `_validate_and_fill`.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: Chooses which command-line action to run. It is the entry point when this file is executed as a script.

**Data flow**: It reads `sys.argv`, which contains the command-line words used to start the script. If there is no valid subcommand, it prints a compact usage message and exits. If the subcommand is recognized, it passes the remaining arguments to the matching command function.

**Call relations**: When the file is run directly, Python calls `main`. `main` looks up the requested action in the subcommand table and hands control to `cmd_extract`, `cmd_preview`, or `cmd_fill`.

*Call graph*: 1 external calls (exit).


### Page Rendering
Renders PDF pages into PNG images for inspection, display, or downstream processing.

### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command-line run`

A PDF is convenient for people to read, but many image-based tools need plain picture files instead. This file solves that by opening a PDF, rendering each page as an image, shrinking very large pages, and saving the results into a chosen folder.

Think of it like a scanner working in reverse: instead of turning paper pages into digital images, it turns PDF pages into PNG files. The rendering is done at 200 DPI, which means “dots per inch” and controls how detailed the image is. After each page is rendered, the file checks its width and height. If either side is bigger than 1000 pixels, it scales the page down while keeping the same shape, so the image is easier to store and use.

The saved files are named in order, such as `page_1.png`, `page_2.png`, and so on. As it works, the script prints what it saved and the final image size. At the end it reports how many pages were rendered. If run directly from the command line, it expects exactly two arguments: the input PDF path and the output folder.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts every page of a PDF file into a PNG image and writes those images into an output folder. It also limits image size so the generated files are not unnecessarily large.

**Data flow**: It receives a PDF file path and an output directory path. It creates the output directory if needed, asks `pdf2image` to turn the PDF pages into images, optionally resizes any page image that is wider or taller than 1000 pixels, then saves each image as `page_N.png`. Its visible output is the set of PNG files on disk plus progress messages printed to the console.

**Call relations**: This is the worker function that does the real conversion. `main` calls it after reading the command-line arguments. Inside, it relies on `pathlib.Path` to create and build file paths, and on `pdf2image.convert_from_path` to do the actual PDF-to-image rendering.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Provides the command-line doorway into the script. It checks that the user supplied the required PDF path and output directory, then starts the rendering work.

**Data flow**: It reads the command-line arguments from `sys.argv`. If the user did not provide exactly two arguments after the script name, it prints a short usage message and exits with an error code. If the arguments are present, it passes them to `render`, which creates the PNG files.

**Call relations**: This function runs when the file is executed directly as a script. It does only the front-desk work: validate the command line, then hand the actual PDF conversion to `render`. If the command is malformed, it calls `sys.exit` to stop immediately.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
