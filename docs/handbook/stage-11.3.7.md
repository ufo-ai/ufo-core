# PDF form, layout, and rendering utilities  `stage-11.3.7`

This stage is shared document support for working with PDFs. It is not the main business logic by itself. Instead, it gives other parts of the system practical tools for seeing, filling, and marking up PDF files, much like a workshop with different tools for the same kind of material.

The form filling tool works with PDFs that already contain hidden fillable fields. It can find those fields, save a field map as JSON, which is a simple text data format, and later fill the PDF using values from that JSON. This turns a hard-to-see PDF form structure into editable data.

The layout tool helps when a PDF is only a static page, with no real form fields. It can inspect page layout, preview where answers should be placed, and write text onto the PDF at chosen positions.

The render tool converts each PDF page into a PNG image. That makes pages easy to view, compare, or pass to tools that work with normal images rather than PDF files.

## Files in this stage

### Fillable form data
Tools for detecting PDF form fields, exporting their structure, and filling them from JSON values.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command invocation`

Fillable PDFs store their fields in a built-in form structure, but that structure can be uneven: some PDFs have a clean form directory, while others have loose page annotations called widgets. This file reads both styles so the project can understand where fields are, what kind they are, and what values are allowed. Without it, the system would not reliably know whether a PDF can be filled natively, what field names to use, or whether a requested checkbox or radio value is valid.

The file defines simple field shapes such as text fields, checkboxes, radio groups, and choice lists. It can scan a PDF, turn each form field into a plain JSON-friendly dictionary, and later use that same information to fill a copy of the PDF. A useful detail is that PDF coordinates start from the bottom of the page, while many layout systems think from the top. The file flips rectangles into a more top-down coordinate style, like translating between two map conventions.

The command-line interface has three jobs: detect whether native fillable fields exist, extract field metadata to JSON, and fill a PDF using JSON entries. Before filling, it checks names, pages, and allowed values so bad data is caught before the output file is written.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has fillable field widgets that are not reported through the normal form directory. This matters because some PDFs are still fillable even when the usual field lookup appears empty.

**Data flow**: It receives a PDF reader, looks through each page's annotations, and searches for widget annotations that also declare a field type. It returns true as soon as it finds one, or false if no such widgets appear.

**Call relations**: The detect command calls this after checking the normal PDF form fields. It gives cmd_detect a fallback way to say, accurately, that a file has fillable fields even when the standard pypdf field list misses them.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the full dotted name of a field by walking from a PDF annotation up through its parent fields. This is needed because PDF forms can store names in pieces, like a folder path split across nested folders.

**Data flow**: It receives one annotation dictionary, reads its own name and each parent name, reverses them into parent-to-child order, and joins them with dots. It returns that full name, or nothing if no name parts exist.

**Call relations**: The AcroForm extraction path calls this while scanning page annotations. The result lets _extract_from_acroform match a visible widget on a page back to the field metadata found earlier.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field information into one of this file's plain field objects. It gives the rest of the code a simple, consistent description instead of requiring every caller to understand PDF field codes.

**Data flow**: It receives a raw PDF field dictionary and a field name, reads the PDF field type code, and creates the matching object: text, checkbox/button, choice list, or an unknown field marker. The output is a FormField-style object ready to be enriched with page and rectangle information.

**Call relations**: Both widget extraction and AcroForm extraction use this as their field factory. When the raw type is a button or choice field, it hands off to _build_checkbox or _build_choice so those field-specific details are interpreted in one place.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which stored value means checked and which means unchecked. This protects later filling from guessing the wrong checkbox value.

**Data flow**: It receives raw PDF checkbox data and a name, reads the available state names, and chooses an on value and an off value when possible. It returns a CheckboxField, and it prints a warning if the checkbox states look non-standard.

**Call relations**: _build_field_from_dict calls this whenever a PDF button field is being treated as a checkbox-style field. The returned object is then used by extraction and validation so users can see and supply the exact checkbox values the PDF expects.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a choice-list field description, including the allowed options when the PDF exposes them. This helps users know what values are acceptable for dropdowns or list boxes.

**Data flow**: It receives raw PDF choice data and a name, reads each listed state, and normalizes it into value/text pairs. It returns a ChoiceField containing those choices.

**Call relations**: _build_field_from_dict calls this for PDF choice fields. Later, extracted JSON can show these choices, and fill validation can reject values that are not in the allowed list.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a checkbox's checked value when it was not available from the normal field data. It looks at the checkbox's appearance settings, which often reveal the real internal value for the checked state.

**Data flow**: It receives a resolved PDF annotation and an existing CheckboxField. If the checkbox already has an on value, it leaves it alone; otherwise it looks for appearance keys other than /Off and writes the first one into the CheckboxField.

**Call relations**: _extract_from_widgets calls this while reading loose widget annotations. It improves the checkbox metadata before that field is returned to the higher-level extraction flow.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from bottom-up coordinates into top-down coordinates. This makes field locations easier to compare with common screen or document layout expectations.

**Data flow**: It receives a rectangle and a page height, converts the rectangle values to numbers, and recalculates the vertical positions by subtracting them from the page height. It returns the converted rectangle.

**Call relations**: Widget extraction, AcroForm extraction, and radio option collection all call this when recording where a field appears on a page. It keeps location data consistent no matter which extraction path found the field.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Reads fillable fields directly from page widget annotations when the normal PDF form directory is missing or incomplete. This is the backup scanner for PDFs with less tidy internal structure.

**Data flow**: It receives a PDF reader, loops through pages and annotations, keeps only field widgets, builds field objects, records page numbers and converted rectangles, and improves checkbox on values when possible. It returns a list of discovered fields.

**Call relations**: _extract_from_acroform calls this when pypdf does not report regular fields. Inside, it uses _build_field_from_dict for field shape, _flip_rect for coordinates, and _extract_checkbox_on_value for checkbox details.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the PDF's fillable form fields into this file's plain field objects. This is the main discovery routine used before exporting metadata or filling values.

**Data flow**: It receives a PDF reader, asks pypdf for the form fields, and falls back to widget scanning if none are found. For normal forms, it builds field objects, finds where they appear on pages, gathers radio button options, skips fields that cannot be located, sorts the final list, and returns it.

**Call relations**: cmd_extract and cmd_fill both call this first because they need a trusted map of the PDF's fields. It coordinates several helpers: _build_field_from_dict to interpret raw fields, _full_field_name to match annotations, _collect_radio_option for radio groups, _flip_rect for positions, and _extract_from_widgets as the fallback path.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one visible radio button choice to a radio group description. Radio buttons are stored as separate widgets, so this function gathers them back into one user-facing group.

**Data flow**: It receives one annotation, the group name, page information, page height, and the shared radio group collection. It finds the annotation's single non-off value, creates the group if needed, converts the option rectangle, and appends that option to the group.

**Call relations**: _extract_from_acroform calls this while scanning page annotations for fields that were identified as radio group candidates. It hands back richer RadioGroup objects through the shared radio_groups dictionary.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a reading-order sort key for extracted fields. It helps the JSON output appear in a useful order: page first, then roughly row by row, then left to right.

**Data flow**: It receives a field, chooses the field rectangle or the first radio option rectangle, groups nearby vertical positions into coarse rows, and returns a tuple used for sorting. It does not change the field.

**Call relations**: The extraction flow uses this as the ordering rule before returning combined fields. That way cmd_extract writes field metadata in an order that is easier for humans to review.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts an internal field object into a plain dictionary that can be written as JSON. This makes extracted PDF metadata portable and easy to inspect or edit.

**Data flow**: It receives a FormField or one of its specialized forms, copies common details such as name, kind, page, and rectangle, then adds checkbox values, radio options, or choice options when relevant. It returns a JSON-friendly dictionary.

**Call relations**: cmd_extract calls this for every extracted field before writing the output file. It is the final translation step from Python objects to the JSON format users see.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a proposed value is allowed for a checkbox, radio group, or choice field. It prevents writing values that the PDF form does not understand.

**Data flow**: It receives a field description and a proposed string value. For fields with limited allowed values, it compares the value against those limits and returns an error message if it is invalid; otherwise it returns nothing.

**Call relations**: _validate_fill_entries calls this while reviewing the user's fill JSON. It supplies the field-specific validation message that cmd_fill uses to stop before creating a bad output PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the detect command, which tells the user whether a PDF appears to contain native fillable fields. This helps decide whether to use form filling or a manual layout-based approach.

**Data flow**: It receives command arguments, checks that exactly one PDF path was supplied, opens the PDF, and looks for normal fields or orphaned widgets. It prints either a positive detection message or guidance that no fillable fields were found; on wrong usage it exits with an error.

**Call relations**: main dispatches here when the user runs the detect subcommand. It relies on pypdf for reading the PDF and calls _has_orphaned_widgets as its backup check.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract command, which writes a JSON map of the PDF's fillable fields. Users can inspect this file to learn field names, pages, locations, and allowed values.

**Data flow**: It receives command arguments, checks for an input PDF and output JSON path, reads the PDF, extracts fields, converts them to dictionaries, creates the output folder if needed, writes formatted JSON, and prints how many fields were written. On wrong usage it exits with an error.

**Call relations**: main dispatches here for the extract subcommand. It calls _extract_from_acroform for discovery and _field_to_dict for the final JSON-ready shape.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill command, which creates a new PDF with form fields filled from a JSON values file. It is the command that turns user-supplied data into a completed fillable PDF.

**Data flow**: It receives command arguments, checks for input PDF, values JSON, and output PDF paths, loads requested values, extracts field metadata, validates the requested entries, groups values by page, writes them into a cloned PDF, creates the output folder if needed, saves the file, and prints a summary. If validation fails, it exits before writing.

**Call relations**: main dispatches here for the fill subcommand. It depends on _extract_from_acroform to know the real fields, _validate_fill_entries to catch mistakes, and pypdf's writer to update field values on each page.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks the full list of requested fill entries before any PDF is written. It catches unknown field names, wrong page numbers, and invalid field values in one pass.

**Data flow**: It receives the user's list of value entries and a lookup table of known fields. For each entry, it checks that the name exists, that any supplied page matches the extracted field page, and that any supplied value is valid for that field. It prints errors as it finds them and returns true if any error occurred.

**Call relations**: cmd_fill calls this after loading the values file and extracting PDF metadata. For field-specific allowed-value checks, it hands each proposed value to _validate_fill_value.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Chooses which command to run based on the command-line arguments. It is the small front door for using this file as a script.

**Data flow**: It reads sys.argv, verifies that a known subcommand was provided, and passes the remaining arguments to the matching command function. If the command is missing or unknown, it prints usage text and exits with an error.

**Call relations**: When the file is run directly, Python calls main through the usual script guard. main then dispatches to cmd_detect, cmd_extract, or cmd_fill through the SUBCOMMANDS table.

*Call graph*: 1 external calls (exit).


### Static PDF layout annotation
Utilities for finding layout features, previewing answer positions, and writing text onto non-fillable PDFs.

### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line document preparation`

Many PDFs look like forms but are not actually fillable. They are just pages with lines, boxes, and printed labels. This file helps with that problem in three steps. First, it can scan a PDF and describe each page: where the words are, where long horizontal rules appear, where small square tick boxes are, and what row-like bands can be inferred between lines. That output can be used to decide where answers should be placed. Second, it can draw a preview image with red and blue rectangles, so a human can check whether the chosen answer areas line up with the original document. Third, it can place text onto the PDF as FreeText annotations, which are PDF overlay notes that look like typed text on the page.

A key detail is coordinate mapping. Images usually count vertical position from the top down, while PDFs often place annotations using a bottom-up coordinate system. The CoordMapper acts like a translator between those two maps, so text lands in the intended box. Before writing the final PDF, the file also checks for common mistakes, such as text boxes that are too short for their font size or boxes that overlap each other. Without this file, the project would have no simple way to inspect static PDF form layouts or safely overlay answers onto them.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: Converts a rectangle from the source coordinate system into the rectangle format needed for a PDF annotation. This matters because image coordinates and PDF annotation coordinates measure vertical position differently.

**Data flow**: It receives a box as four numbers. If the box came from an image, it scales the box to the PDF page size and flips the vertical coordinates so top-down image positions become bottom-up PDF positions. If the box already uses PDF-style coordinates, it only flips the vertical position into the annotation format. It returns the converted rectangle.

**Call relations**: When text is about to be written onto a PDF, _validate_and_fill creates a CoordMapper for the page and asks this method to translate the requested content area. The returned rectangle is then handed to the PDF annotation object so the text appears in the right place.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: Reads one PDF page and pulls out the layout clues that are useful for understanding a form: words, long horizontal lines, and small square boxes that look like checkboxes.

**Data flow**: It receives one page from pdfplumber and the page number. It creates a PageLayout record with the page size, then looks through the page objects. Long lines are saved as horizontal rules, small near-square rectangles are saved as tick boxes, and extracted words are saved with their positions. It returns the filled PageLayout.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. This function relies on pdfplumber page data and its word extraction, then passes the page summary back so row ranges can be added and the whole document can later be written as JSON.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: Finds vertical bands between detected horizontal lines, which can help describe table-like rows on a form.

**Data flow**: It receives a PageLayout that already has horizontal rule positions. It sorts those line positions from top to bottom, then records each space between two neighboring lines as a row range with a top, bottom, and height. It changes the PageLayout in place and returns nothing.

**Call relations**: _extract_all_pages calls this right after _extract_page. In the larger flow, _extract_page finds the raw line positions, and this function turns those positions into more meaningful row regions before the page is exported.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: Scans an entire PDF and builds layout summaries for every page.

**Data flow**: It receives a PDF file path. It opens the PDF with pdfplumber, loops through the pages, extracts each page layout, computes row ranges for that page, and gathers all page layouts into a list. It returns that list.

**Call relations**: cmd_extract calls this when the user runs the extract command. This function is the bridge between the command-line request and the lower-level page scanning functions.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: Turns PageLayout objects into ordinary dictionaries that can be saved as JSON.

**Data flow**: It receives a list of PageLayout records. For each page, it copies the page number, size, text elements, horizontal rules, tick boxes, and row ranges into a plain dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after _extract_all_pages has scanned the PDF. The result is then passed to JSON writing, because JSON works naturally with plain lists and dictionaries rather than custom Python objects.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: Checks a field-definition file for obvious placement problems, then writes the requested text annotations onto a PDF.

**Data flow**: It receives paths for the input PDF, the JSON field description, and the output PDF. It reads the JSON, opens the PDF, records page sizes, then loops over the requested form fields. For each field with text, it checks that the content area is tall enough and does not overlap earlier placed areas. If the field is valid, it converts the field rectangle into PDF annotation coordinates, creates a FreeText annotation, and adds it to the PDF writer. If any errors were found, it prints them and stops. Otherwise it writes the new PDF file and prints a summary.

**Call relations**: cmd_fill calls this when the user runs the fill command. Inside the fill process, it uses _rects_overlap to catch collisions, CoordMapper.to_annotation_rect to translate coordinates, and pypdf objects to read, annotate, and write the PDF.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: Answers a simple question: do two rectangles touch or cover any of the same space?

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges. If one rectangle is completely to the side of or above the other, they do not overlap; otherwise they overlap. It returns true or false.

**Call relations**: _validate_and_fill uses this while checking new fields against fields already placed on the same page. It is the small geometry test that helps prevent one answer box from being written over another.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract command, which scans a PDF and saves its layout information to a JSON file.

**Data flow**: It receives command-line arguments after the word extract. If the user did not provide exactly an input PDF and output JSON path, it prints the correct usage and exits. Otherwise it scans the PDF, converts the page summaries to JSON-friendly data, writes the output file, and prints counts of what it found.

**Call relations**: main calls this when the first command-line word is extract. It hands the real PDF scanning to _extract_all_pages and the JSON shaping to _pages_to_dict, then takes care of saving the result for the user.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: Implements the preview command, which draws proposed form-field boxes onto an image so a person can visually check the layout.

**Data flow**: It receives command-line arguments after the word preview: page number, fields JSON, input image, and output image. It reads the field definitions, opens the image, draws red rectangles around content areas and blue rectangles around label boxes for the chosen page, saves the marked-up image, and prints how many fields were highlighted.

**Call relations**: main calls this when the user chooses preview. Unlike the PDF-writing path, this function works with an image and drawing tools so a human can inspect the placement before committing annotations to a PDF.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill command, which writes text from a field-definition JSON file onto a PDF.

**Data flow**: It receives command-line arguments after the word fill. If the user did not provide input PDF, fields JSON, and output PDF paths, it prints the correct usage and exits. Otherwise it passes those paths to _validate_and_fill, which performs the checks and writes the final PDF.

**Call relations**: main calls this when the first command-line word is fill. This command function is intentionally thin: it validates the number of arguments, then delegates the real PDF annotation work to _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: Chooses which command to run based on the user's command-line input.

**Data flow**: It reads the program arguments from sys.argv. If there is no command or the command is not one of the known choices, it prints a usage message and exits. Otherwise it looks up the matching command function and passes along the remaining arguments.

**Call relations**: This is the file's command-line entry point. When the script is run directly, main dispatches to cmd_extract, cmd_preview, or cmd_fill, which then carry out the requested PDF layout task.

*Call graph*: 1 external calls (exit).


### Page image rendering
A utility for converting PDF pages into PNG images for viewing or downstream image-based processing.

### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `on-demand command-line use`

This script solves a practical problem: many tools can work with images more easily than with PDF pages. Given a PDF and an output folder, it renders each page into a PNG file named like page_1.png, page_2.png, and so on. Without this file, another part of the system or a user would need a separate way to preview, inspect, or process PDF pages as images.

The flow is simple. First, it makes sure the requested output folder exists. Then it asks the pdf2image library to convert the PDF pages into image objects at 200 DPI, meaning a fairly detailed rendering. For each page image, it checks the width and height. If either side is larger than 1000 pixels, it shrinks the image while keeping the same shape, like reducing a poster to fit inside a frame without stretching it. Finally, it saves the page as a PNG file and prints a short progress message showing where the file went and what size it is.

The file can also be run directly from the command line. In that mode, it expects exactly two arguments: the input PDF path and the output directory.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts every page of a PDF into a PNG image file. It also keeps very large page images within a 1000-pixel maximum side length so the output is easier to store, display, and process.

**Data flow**: It receives a PDF file path and an output folder path. It creates the folder if needed, reads the PDF through the PDF-to-image library, optionally resizes each rendered page image, then writes one PNG file per page into the folder. Its visible output is the saved image files plus progress text printed to the console.

**Call relations**: This is the worker function that does the actual conversion. The command-line wrapper, main, calls it after checking that the user supplied the right number of command-line arguments. Inside the conversion flow, it relies on pathlib.Path to work with folders and pdf2image.convert_from_path to do the heavy PDF rendering work.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the script. It checks that the user gave the two required pieces of information: the PDF to read and the folder to write images into.

**Data flow**: It reads the command-line arguments from sys.argv. If the arguments are missing or there are too many, it prints a usage message and exits with an error code. If the arguments are correct, it passes the input PDF path and output directory to render, which creates the PNG files.

**Call relations**: This function is called when the file is run directly as a script. It does only the front-door work: validate the command shape, stop early with sys.exit when it is wrong, or hand off to render when it is right.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
