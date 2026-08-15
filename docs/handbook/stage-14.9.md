# PDF form, layout, and rendering utilities  `stage-14.9`

This stage provides the PDF toolbox used when the system needs to inspect, fill, preview, or render PDF documents. It is not the main work loop by itself. Instead, it is behind-the-scenes support that other parts of the project can call when a PDF must be turned into something easier to understand or modify.

The formfill tool works with PDFs that already contain native fillable fields, like digital boxes for names, dates, or checkmarks. It can check whether those fields exist, export their names and details to JSON, which is a simple text format for structured data, and fill the PDF using values from that JSON.

The layout tool is for PDFs that only look like forms. These have no real fields, just page graphics. It scans the page for layout clues, can draw a preview of where fields should go, and can add text annotations onto the PDF.

The render tool converts PDF pages into PNG images, so pages can be viewed or processed like ordinary pictures.

## Files in this stage

### Native PDF Forms
Tools for detecting, extracting, and filling real PDF form fields backed by native fillable form data.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command-line execution`

PDF forms can contain built-in fields, such as text boxes, checkboxes, radio buttons, and drop-down choices. This file gives the project a way to inspect and fill those fields without guessing where text should be drawn on the page. Without it, the system would have to fall back to manual placement, which is more fragile and harder to automate.

The file works like a three-button tool. The detect command opens a PDF and checks whether it contains real form fields. The extract command reads the PDF’s internal form data and writes a JSON description of each field: its name, type, page, screen position, and allowed values where needed. The fill command reads that same style of JSON, checks that the requested values are valid, and writes a new PDF with the fields filled in.

A few helper functions translate PDF internals into simpler project-friendly shapes. For example, PDF coordinates start from the bottom-left of the page, so _flip_rect changes field rectangles into a more familiar top-down layout. The code also deals with awkward PDFs whose field information is stored directly on page widgets instead of in the normal form table. In short, this file is the bridge between messy PDF form structures and clean JSON that other tools can understand.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has fillable field widgets that are not reported through the normal form-field list. This matters because some PDFs still contain usable fields even when the usual PDF library lookup says there are none.

**Data flow**: It receives an open PDF reader, walks through each page, and looks at the page annotations. If it finds an annotation marked as a form widget with a field type, it returns true; otherwise it returns false after checking all pages.

**Call relations**: cmd_detect uses this as a backup check. First it asks the PDF reader for normal fields; if that does not find any, this function looks directly at the page-level widgets before the command tells the user there are no fillable fields.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the full name of a field when a PDF stores it in a parent-child structure. This is like reconstructing a full street address from apartment, building, and street parts.

**Data flow**: It receives a field annotation, reads its own name piece, then walks up through any parent fields collecting their name pieces. It returns the pieces joined with dots, or nothing if no name pieces exist.

**Call relations**: _extract_from_acroform calls this while matching page annotations back to the fields found in the PDF’s form table. The full name lets the extractor connect the visual widget on a page with the field metadata.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field data into one of this file’s simpler field objects. It identifies whether a field is text, button-like, choice-based, or unknown.

**Data flow**: It receives a raw PDF field dictionary and a field name. It reads the PDF field type code, then returns a FormField, CheckboxField, ChoiceField, or an unknown FormField depending on that code.

**Call relations**: Both _extract_from_acroform and _extract_from_widgets call this when they discover a field. It delegates checkbox details to _build_checkbox and choice-list details to _build_choice so the rest of the extractor can work with clean field objects.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which stored value means checked and which means unchecked. This is important because PDFs do not always use the same word for the checked state.

**Data flow**: It receives raw PDF button-field data and a name. It reads the available states, chooses an on value and an off value when possible, warns if the states look unusual, and returns a CheckboxField.

**Call relations**: _build_field_from_dict calls this whenever it sees a PDF button field. The resulting CheckboxField later helps extraction show valid values and helps filling reject invalid checkbox values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description of a choice field, such as a drop-down list or list box. It records the values a user is allowed to choose.

**Data flow**: It receives raw PDF choice-field data and a name. It reads the available states, converts each option into a simple value-and-display-text dictionary, and returns a ChoiceField.

**Call relations**: _build_field_from_dict calls this for PDF choice fields. The extracted choices are later written to JSON by _field_to_dict and checked during filling by _validate_fill_value.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a checkbox’s checked value when the normal field data did not provide it. It looks at the checkbox’s appearance settings, where PDFs often store the checked-state name.

**Data flow**: It receives a resolved PDF widget and a CheckboxField object. If the checkbox already has an on value, it does nothing; otherwise it reads the widget’s appearance keys, chooses the non-Off key as the checked value, and updates the CheckboxField in place.

**Call relations**: _extract_from_widgets calls this after building a checkbox from page-level widget data. It improves the checkbox metadata before the field is returned to the extractor’s caller.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from bottom-up coordinates into top-down coordinates. This makes field positions easier for humans and many layout tools to understand.

**Data flow**: It receives a rectangle and the page height. It converts the rectangle numbers to floats, keeps the left and right values, flips the vertical values using the page height, and returns the converted rectangle.

**Call relations**: _extract_from_widgets, _extract_from_acroform, and _collect_radio_option call this whenever they record where a field appears on a page. It gives all extracted field locations a consistent coordinate style.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts fields directly from page annotations when the PDF’s normal form table is missing or incomplete. This is a fallback for less tidy PDFs.

**Data flow**: It receives an open PDF reader, walks each page, and inspects page annotations. For each annotation that is a form widget with a name and type, it builds a field object, records its page and rectangle, improves checkbox values if needed, and returns the list of fields found.

**Call relations**: _extract_from_acroform calls this when reader.get_fields does not return useful form data. It uses _build_field_from_dict to classify fields, _flip_rect to convert locations, and _extract_checkbox_on_value to complete checkbox metadata.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the main list of fillable fields from a PDF’s AcroForm data, which is the standard place PDFs store form definitions. It also connects those definitions to their page positions.

**Data flow**: It receives an open PDF reader and asks it for form fields. If none are found, it falls back to widget extraction. Otherwise it builds field objects, tracks possible radio-button groups, scans page annotations to find locations, collects radio options, skips fields that cannot be located, sorts the result, and returns the final field list.

**Call relations**: cmd_extract and cmd_fill rely on this as the main PDF-reading step. Inside, it calls _build_field_from_dict for ordinary fields, _full_field_name to match annotations to names, _flip_rect for positions, _collect_radio_option for radio groups, and _extract_from_widgets as a fallback.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one radio-button choice to a radio group description. Radio buttons are stored as several small widgets that share one field name, so this gathers those pieces into one group.

**Data flow**: It receives a page annotation, the group name, page number, page height, and the shared radio-group dictionary. It reads the annotation’s checked-state value, creates the RadioGroup if needed, converts the option rectangle, and appends the option to that group.

**Call relations**: _extract_from_acroform calls this while scanning page annotations for fields that looked like radio groups. It uses _flip_rect so each option’s location is stored in the same coordinate system as other fields.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a stable, human-friendly order for extracted fields. It sorts mostly by page, then by row, then by left-to-right position.

**Data flow**: It receives a field object. It chooses the field’s rectangle, or the first radio option’s rectangle for a radio group, rounds the vertical position into a row bucket, and returns a tuple used for sorting.

**Call relations**: _extract_from_acroform uses this when it sorts the final combined list of normal fields and radio groups. The result makes the JSON output easier to read because fields appear roughly in page order.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts an internal field object into plain JSON-friendly data. This is what makes extracted fields usable outside Python.

**Data flow**: It receives a FormField or one of its specialized versions. It writes common information like name, kind, page, and rectangle, then adds checkbox values, radio options, or choice options when those apply, and returns a dictionary.

**Call relations**: cmd_extract calls this for every field found by _extract_from_acroform. Its output is collected and written to the JSON file that users can inspect or later use as a template for filling.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a requested value is allowed for a specific field. This prevents writing a PDF with impossible checkbox, radio, or choice values.

**Data flow**: It receives a field description and a proposed string value. For checkboxes, radio groups, and choice fields, it compares the value against the allowed values and returns an error message if it is invalid; otherwise it returns nothing.

**Call relations**: _validate_fill_entries calls this while checking the user’s fill JSON. It is the field-type-specific part of validation before cmd_fill writes the output PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the detect command. It tells the user whether a PDF appears to contain native fillable fields.

**Data flow**: It receives command-line arguments, expects one PDF path, and exits with a usage message if the arguments are wrong. It opens the PDF, checks normal form fields and orphaned widgets, then prints either that fillable fields were found or that manual layout may be needed.

**Call relations**: main dispatches to this when the user runs the detect subcommand. It calls _has_orphaned_widgets only if needed to catch PDFs whose fields are stored outside the usual form list.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract command. It reads a fillable PDF and writes a JSON inventory of the fields that can be filled.

**Data flow**: It receives command-line arguments, expects an input PDF and output JSON path, and exits with a usage message if the arguments are wrong. It opens the PDF, extracts field metadata, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written.

**Call relations**: main dispatches to this when the user runs the extract subcommand. It relies on _extract_from_acroform for PDF understanding and _field_to_dict for producing clean JSON-ready records.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill command. It takes a PDF and a JSON list of desired field values, then writes a filled copy of the PDF.

**Data flow**: It receives command-line arguments, expects input PDF, values JSON, and output PDF paths, and exits with a usage message if needed. It reads the requested values, extracts the real field metadata from the PDF, validates names, pages, and allowed values, groups values by page, asks the PDF writer to update those fields, and saves the new PDF.

**Call relations**: main dispatches to this when the user runs the fill subcommand. It calls _extract_from_acroform so it knows what fields exist, _validate_fill_entries to stop bad input early, and then hands valid page-by-page values to pypdf’s writer.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks the whole fill-values JSON list before the PDF is modified. It catches unknown field names, wrong page numbers, and invalid values.

**Data flow**: It receives the user’s value entries and a lookup table of real fields by name. For each entry, it checks that the field exists, that the page matches if a page was supplied, and that the value is allowed if a value was supplied. It prints errors as it finds them and returns true if any error occurred.

**Call relations**: cmd_fill calls this after reading the user’s JSON and extracting PDF metadata. It calls _validate_fill_value for field-type-specific value checks, and cmd_fill exits instead of writing a PDF if this function reports errors.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Chooses which command to run based on the command-line input. It is the script’s front door.

**Data flow**: It reads sys.argv, checks that the user provided a known subcommand, and prints a usage message and exits if not. If the subcommand is valid, it passes the remaining arguments to the matching command function.

**Call relations**: This runs when the file is executed directly as a script. It dispatches to cmd_detect, cmd_extract, or cmd_fill through the SUBCOMMANDS table.

*Call graph*: 1 external calls (exit).


### Visual Form Layouts
Utilities for PDFs that resemble forms visually, including layout scanning, preview drawing, and text annotation placement.

### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line PDF extraction, preview, and fill runs`

Many PDFs are visually forms, but to software they are just pages with lines, boxes, and words. This file helps bridge that gap. It first reads a PDF page like a picture with structure: words, long horizontal lines, and small square boxes that probably act like checkboxes. It stores those findings in a simple page layout record so another step can decide where answers should go.

The tool has three commands. The extract command scans a PDF and writes a JSON file describing page sizes, text positions, horizontal rules, checkbox-like boxes, and row bands between long lines. The preview command takes a field definition JSON file and draws colored rectangles on an image, so a person can quickly check whether the planned answer boxes line up. The fill command takes the original PDF and the field definitions, checks for obvious problems such as overlapping boxes or text areas that are too short, then adds invisible-border text annotations at the requested places.

One important wrinkle is coordinate systems. Images often count from the top-left corner, while PDFs place annotations using a bottom-left origin. CoordMapper is the translator between those two maps, like converting directions from “walk north from the front door” to “move up from the basement corner.” Without that conversion, text would appear in the wrong place.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: Converts a rectangle from the field definition into the coordinate format needed for a PDF annotation. This matters because PDF pages and image previews can measure positions from different corners of the page.

**Data flow**: It receives a box as four numbers, plus the mapper’s stored PDF size and source coordinate settings. If the box came from an image, it scales the box to the PDF page size and flips the vertical direction; if it already came from PDF-style coordinates, it only flips the vertical direction. It returns a four-number rectangle ready to give to the PDF annotation library.

**Call relations**: During the fill command, _validate_and_fill builds a CoordMapper for the page being written and asks this method to translate each field’s content area before creating the text annotation.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: Reads one PDF page and collects the visual clues that make it possible to understand where form-like areas are. It looks for long horizontal lines, small square checkbox-like boxes, and words with their positions.

**Data flow**: It takes a page object from the PDF-reading library and a page number. It creates a PageLayout record, scans line objects for long horizontal rules, scans rectangle objects for small near-square tick boxes, and asks the page for extracted words. It returns the filled PageLayout for that page.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. After _extract_page has collected the raw layout clues, _extract_all_pages passes the result to _compute_row_ranges so row bands can be added.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: Finds the vertical bands between horizontal rules on a page. These bands are useful because form tables often use long lines to separate rows.

**Data flow**: It reads the horizontal rule positions already stored in a PageLayout. It sorts their vertical positions, pairs each neighboring line, and adds a row range with a top, bottom, and height. It changes the PageLayout in place and does not return a separate value.

**Call relations**: _extract_all_pages calls this right after _extract_page finishes each page. It builds on the line data that _extract_page found, turning individual lines into row-shaped regions.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: Scans an entire PDF and turns every page into a structured layout description. This is the main worker behind the extract command.

**Data flow**: It receives a PDF file path. It opens the PDF, loops through every page, uses _extract_page to gather layout clues, uses _compute_row_ranges to add row bands, and collects all PageLayout records into a list. It returns that list.

**Call relations**: cmd_extract calls this after checking the command-line arguments. Inside the scan, it coordinates the per-page extraction and row-range calculation before handing the full result back to cmd_extract for saving.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: Turns PageLayout objects into plain dictionaries that can be written as JSON. This makes the extracted layout easy for people and other tools to read.

**Data flow**: It receives a list of PageLayout records. For each page, it copies the page number, size, text elements, horizontal rules, tick boxes, and row ranges into ordinary dictionary form. It returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after _extract_all_pages has finished scanning the PDF. The returned plain data is then passed to JSON writing code.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: Checks a field definition file and writes text annotations into a PDF if the definitions look safe enough. It prevents common mistakes, such as overlapping fields or text boxes that are too short for the chosen font size.

**Data flow**: It receives paths for the input PDF, the fields JSON file, and the output PDF. It reads the JSON, opens the PDF, records each page’s size, and then walks through every requested form field. For each field with text, it checks height and overlap, translates the field rectangle into PDF annotation coordinates, creates a FreeText annotation, and adds it to the right page. If errors are found, it prints them and exits; otherwise it writes the new PDF file.

**Call relations**: cmd_fill calls this after validating the fill command’s arguments. This function uses _rects_overlap during safety checks, uses CoordMapper to translate coordinates, and then hands the final annotation objects to the PDF writer.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: Answers a simple question: do two rectangular areas touch or cover the same space? It is used to catch field definitions that would put text on top of other text or labels.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges to see whether one is completely to the side or above the other. It returns true when the rectangles overlap and false when they do not.

**Call relations**: _validate_and_fill calls this while checking each new field against areas that have already been placed on the same page.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract subcommand. It scans a PDF and saves a JSON description of the page layout.

**Data flow**: It receives the command-line arguments after the word extract. If the user did not provide an input PDF and an output JSON path, it prints usage help and exits. Otherwise it scans the PDF with _extract_all_pages, converts the result with _pages_to_dict, writes the JSON file, and prints a short summary of what was found.

**Call relations**: main dispatches to this function when the user runs layout.py extract. It is the command-level wrapper around the lower-level page scanning and JSON conversion helpers.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: Implements the preview subcommand. It draws the planned form fields on top of an image so a person can visually check alignment before modifying a PDF.

**Data flow**: It receives a page number, a fields JSON path, an input image path, and an output image path. It reads the field definitions, opens the image, draws red rectangles for content areas and blue rectangles for label boxes on the requested page, saves the marked-up image, and prints how many fields were highlighted.

**Call relations**: main dispatches to this function when the user runs layout.py preview. Unlike the fill path, it does not edit a PDF; it uses the same field definitions as a visual dry run.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill subcommand. It is the small command wrapper that checks the user supplied the right paths and then starts the PDF-writing process.

**Data flow**: It receives the command-line arguments after the word fill. If the input PDF, fields JSON, and output PDF paths are not all present, it prints usage help and exits. Otherwise it passes those three paths to _validate_and_fill.

**Call relations**: main dispatches to this function when the user runs layout.py fill. It hands off the real validation and annotation work to _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: Chooses which command to run based on the command line. It is the entry point when this file is executed as a script.

**Data flow**: It reads sys.argv, which contains the command-line words used to start the program. If no known subcommand is present, it prints the valid choices and exits. Otherwise it looks up the matching command function and passes along the remaining arguments.

**Call relations**: The Python runtime calls this when the file is run directly. It dispatches to cmd_extract, cmd_preview, or cmd_fill, which then perform the requested PDF task.

*Call graph*: 1 external calls (exit).


### Page Rendering
A rendering utility that converts PDF pages into PNG images for viewing or downstream image-based processing.

### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command-line document rendering`

This script solves a practical conversion problem: many tools work better with images than with PDF pages. Given a PDF file and an output folder, it renders every page as a PNG image. Without it, anything downstream that expects page images would first need its own PDF-to-image conversion step.

The flow is simple. The command-line entry point checks that the user gave exactly two pieces of information: the input PDF path and the folder where images should be written. The render function then creates that folder if it does not already exist. It asks the pdf2image library to read the PDF and produce one image per page at 200 DPI, meaning 200 dots per inch, a quality setting for raster images.

Before saving each page, the script checks its width and height. If either side is larger than 1000 pixels, it shrinks the image while keeping the same proportions. This is like photocopying a large page down to fit inside a fixed-size frame without stretching it. Each page is saved as page_1.png, page_2.png, and so on, and the script prints progress messages showing where each file went and its final size.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts a PDF into one PNG image per page and writes those images into a chosen folder. It also limits very large rendered pages to a maximum size so the output images stay manageable.

**Data flow**: It receives a PDF file path and an output directory path. It creates the output directory if needed, reads the PDF into page images, optionally shrinks each image if it is wider or taller than 1000 pixels, then saves each page as a numbered PNG file. Its visible outputs are the image files on disk and progress messages printed to the console.

**Call relations**: This is the worker function that does the actual conversion. The command-line wrapper calls it after checking the user supplied the right arguments. Inside, it relies on the filesystem path helper to prepare the output folder and on the external pdf2image library to turn PDF pages into images.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Acts as the command-line front door for the script. It checks the user’s arguments and starts the PDF rendering process if they are valid.

**Data flow**: It reads the command-line arguments from the running process. If the user did not provide exactly an input PDF and an output folder, it prints a usage message and exits with an error code. If the arguments are present, it passes them to render and lets that function create the images.

**Call relations**: This function runs when the file is executed directly as a script. It does not perform the conversion itself; it validates the command shape, then hands off to render. If the command is malformed, it stops the program early through the system exit call.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
