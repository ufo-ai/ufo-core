# PDF form, layout, and rendering helpers  `stage-17.5`

This stage provides practical command-line helpers for working with PDFs. It is shared support rather than the main work loop: other tools or people can call these scripts when they need to inspect a PDF, fill it in, mark it up, or turn it into images.

The formfill tool works with true fillable PDFs, meaning files that contain built-in form fields such as text boxes or checkboxes. It can check whether those fields exist, export a JSON field map that names them, and create a filled PDF from JSON values. JSON is a simple text format for structured data.

The layout tool is for “fake” forms: PDFs that look like forms but have no real fields. It scans the page layout, helps preview where text should go, and writes text annotations onto the PDF, like placing labels on top of a printed page.

The render tool converts each PDF page into a PNG image. Together, these tools cover three common PDF paths: fill real forms, annotate visual forms, and make page images for visual review or later processing.

## Files in this stage

### Fillable form utilities
Command-line helpers for detecting, exporting, and filling real PDF form fields.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command-line invocation`

PDF forms are not just pictures of boxes. Many contain hidden form fields, such as text boxes, checkboxes, radio buttons, and drop-down choices. This file reads those hidden fields so another tool or person can fill them reliably, instead of guessing where text should be placed on the page. Think of it like asking the PDF for its built-in form labels before writing on it.

The file uses pypdf, a Python library for reading and writing PDF files. It supports three command-line actions. “detect” checks whether a PDF appears to contain fillable fields. “extract” reads the fields and writes a JSON description with names, kinds, page numbers, positions, and allowed values. “fill” reads that field description indirectly from the PDF, checks a user-provided JSON list of values, and writes a new filled PDF.

A lot of the code exists because PDFs store form fields in more than one way. Some fields are in the main AcroForm structure, which is the PDF’s standard form registry. Others appear only as page annotations called widgets, which are visible form controls on a page. The file tries the standard route first, then falls back to widgets when needed. It also normalizes page coordinates so the exported rectangles are easier to use in top-down page layout.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has visible form controls that are not reported through the normal form-field list. This matters because some PDFs contain fillable-looking widgets even when the standard field registry looks empty.

**Data flow**: It receives a PDF reader, looks through every page, then checks each page annotation for a widget with a field type. It returns true as soon as it finds one, or false if no such widget appears.

**Call relations**: The detect command calls this after asking pypdf for normal fields. If the normal route finds nothing, this helper gives the file one more chance to recognize a fillable PDF before telling the user to use manual layout instead.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the full name of a form field by walking up its parent chain. This is needed because some PDF fields store their name in pieces, like folders in a path.

**Data flow**: It receives a PDF annotation dictionary, reads its own name part, then follows its parent links and collects any parent name parts. It returns a dotted name such as “section.field”, or nothing if no name parts exist.

**Call relations**: The AcroForm extraction path uses this while scanning page annotations. It lets that scan match a visible widget on the page back to the field metadata that was already found in the PDF’s form registry.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field data into one of this file’s plain Python field objects. It gives the rest of the code a simpler label such as text, checkbox, choice, or unknown instead of raw PDF codes.

**Data flow**: It receives a raw PDF field dictionary and a field name. It reads the PDF field type code, then creates a text field directly, delegates button fields to checkbox building, delegates choice fields to choice building, or creates an unknown field if the type is unfamiliar.

**Call relations**: Both main extraction paths call this when they discover a field. It hands checkbox and choice details to _build_checkbox and _build_choice so the extraction code can stay focused on finding fields rather than interpreting every field type itself.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which stored value means checked and which means unchecked. This is important because PDFs often use custom names for the checked state.

**Data flow**: It receives raw button-field data and a name. It reads the available state names, chooses “/Off” as the unchecked value when present, warns if the states look unusual, and returns a CheckboxField with the best known on and off values.

**Call relations**: _build_field_from_dict calls this when it sees a PDF button field. The resulting CheckboxField is later exported to JSON or used to validate values before filling a PDF.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a drop-down or list-style choice field. It records the allowed stored values and the human-readable text when the PDF provides both.

**Data flow**: It receives raw choice-field data and a name. It loops over the PDF’s available states, converts each option into a small value/text dictionary, and returns a ChoiceField containing those options.

**Call relations**: _build_field_from_dict calls this when it sees a PDF choice field. The choices it returns are later written to JSON for users and checked by the fill validation step.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a missing checkbox checked value by looking at the checkbox’s appearance data. In PDFs, the visual appearances often reveal the real value needed to turn a box on.

**Data flow**: It receives a resolved PDF annotation and a CheckboxField object. If the checkbox already has an on value, it does nothing. Otherwise it reads the annotation’s normal appearance keys, picks the first non-“/Off” key as the checked value, and updates the CheckboxField in place.

**Call relations**: _extract_from_widgets calls this when it builds checkbox fields directly from page widgets. It supplements _build_checkbox when the raw field dictionary did not already expose enough state information.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from PDF-style coordinates into top-down page coordinates. This makes exported positions easier for people and layout tools to understand.

**Data flow**: It receives a rectangle and the page height. It turns the four rectangle numbers into floats, then flips the vertical positions by subtracting them from the page height. It returns a new rectangle list.

**Call relations**: The widget extractor, AcroForm extractor, and radio-option collector all call this before storing field positions. It is the shared ruler-conversion step for every field rectangle this tool exports.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Finds fillable fields by looking directly at page-level widget annotations. This is the fallback path for PDFs whose form controls are visible on pages but not listed in the normal form registry.

**Data flow**: It receives a PDF reader, walks through each page and its annotations, keeps only widget annotations with a field type, builds a field object, records its page and rectangle, fixes checkbox on-values when possible, and returns the list of fields.

**Call relations**: _extract_from_acroform calls this when pypdf reports no standard fields. Inside the fallback, it uses _build_field_from_dict to classify fields, _flip_rect to normalize positions, and _extract_checkbox_on_value to improve checkbox metadata.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the usable form-field map from a PDF’s standard AcroForm data. It is the main discovery step used before exporting fields or filling values.

**Data flow**: It receives a PDF reader and asks pypdf for the PDF’s form fields. If none are found, it falls back to widget extraction. Otherwise it builds field objects, tracks possible radio-button groups, scans page annotations to find where each field appears, collects radio options, warns about fields that cannot be located, sorts the final list, and returns it.

**Call relations**: The extract and fill commands both call this as their source of truth about the PDF. It coordinates several helpers: _build_field_from_dict for field objects, _full_field_name for matching widgets to fields, _flip_rect for coordinates, _collect_radio_option for radio groups, and _extract_from_widgets as a fallback.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one visible radio-button option to a radio group description. Radio buttons are special because several separate widgets together represent one field with multiple choices.

**Data flow**: It receives one annotation, the group name, page information, page height, and the shared radio-group dictionary. It reads the annotation’s non-off appearance value, creates the RadioGroup if needed, flips the option rectangle, and appends the option value and position to the group.

**Call relations**: _extract_from_acroform calls this while scanning page annotations that belong to radio-button candidates. It uses _flip_rect so radio options are reported with the same coordinate style as other fields.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides an ordering rule so extracted fields appear in a page-like reading order. This makes the JSON easier to inspect and edit by humans.

**Data flow**: It receives a field. For radio groups it uses the first option’s rectangle; for other fields it uses the field rectangle. It groups nearby vertical positions into rows, then returns page number, row, and left position as the sorting key.

**Call relations**: This helper is used when the extracted field list is arranged for output. It does not change fields; it only supplies the comparison values that put them in a sensible order.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts one internal field object into a JSON-friendly dictionary. This is how the tool turns Python objects into information that can be written to a file.

**Data flow**: It receives a FormField or one of its specialized versions. It starts with the name and kind, adds page and rectangle when known, then adds checkbox values, radio options, or choice lists depending on the field type. It returns a plain dictionary.

**Call relations**: The extract command calls this for every field returned by _extract_from_acroform. Its output is then passed to JSON writing so users can see and reuse the field map.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a proposed value is allowed for fields that have fixed choices. This prevents writing a checkbox, radio button, or drop-down value that the PDF does not understand.

**Data flow**: It receives a field description and a proposed string value. For checkboxes it compares against the known on and off values; for radio groups and choices it compares against the listed option values. It returns an error message if invalid, or nothing if the value is acceptable.

**Call relations**: _validate_fill_entries calls this while checking the user’s fill JSON. It is the focused rule-checker for individual field values before cmd_fill writes anything to the output PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the “detect” command, which tells the user whether a PDF appears to contain fillable fields. It is a quick yes/no check before doing extraction or manual layout work.

**Data flow**: It receives command-line arguments, expects exactly one PDF path, opens the PDF, checks for standard fields and orphaned widgets, then prints either a fillable-fields message or a message saying no fields were found. If the arguments are wrong, it prints usage and exits with an error.

**Call relations**: The main dispatcher calls this when the user chooses “detect”. It uses pypdf to open the PDF and calls _has_orphaned_widgets only when the standard field check is not enough.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the “extract” command, which writes a JSON map of all fillable fields in a PDF. Users can inspect this file to learn field names, pages, positions, and allowed values.

**Data flow**: It receives command-line arguments, expects an input PDF and output JSON path, opens the PDF, extracts fields, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written. If the arguments are wrong, it prints usage and exits.

**Call relations**: The main dispatcher calls this when the user chooses “extract”. It relies on _extract_from_acroform for discovery and _field_to_dict for turning the discovered fields into JSON-ready data.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the “fill” command, which creates a new PDF with form fields filled from a JSON value file. It validates the requested values first so obvious mistakes are caught before writing.

**Data flow**: It receives command-line arguments, expects an input PDF, a values JSON file, and an output PDF path. It reads the values, extracts the PDF’s field metadata, validates every requested entry, groups values by page, clones the original PDF into a writer, updates each page’s form fields, creates the output folder, writes the new PDF, and prints a summary. If arguments or validation fail, it exits with an error.

**Call relations**: The main dispatcher calls this when the user chooses “fill”. It uses _extract_from_acroform to know what fields exist, _validate_fill_entries to check the user’s JSON, and pypdf’s writer to produce the final filled PDF.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks the whole list of user-supplied fill entries before the PDF is written. It protects against bad field names, wrong page numbers, and invalid choice values.

**Data flow**: It receives a list of value entries and a lookup table of known fields by name. For each entry it checks that the field exists, that the page matches when supplied, and that the value is valid when present. It prints any errors it finds and returns true if there was at least one error, otherwise false.

**Call relations**: cmd_fill calls this before updating the PDF. For value-specific rules it hands each value to _validate_fill_value, then reports the combined validation result back to cmd_fill so filling can stop safely if needed.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Acts as the command-line front door for the script. It chooses which command to run based on the first word the user typed after the script name.

**Data flow**: It reads the process arguments from sys.argv. If no valid subcommand is present, it prints the allowed usage and exits with an error. Otherwise it passes the remaining arguments to the selected command function.

**Call relations**: This runs when the file is executed directly as a script. It dispatches to the command stored in SUBCOMMANDS, such as cmd_detect, cmd_extract, or cmd_fill, and uses sys.exit when the command name is missing or unknown.

*Call graph*: 1 external calls (exit).


### Pseudo-form layout annotation
Command-line helpers for placing text onto PDFs that visually resemble forms but lack true fillable fields.

### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line PDF extraction, preview, and fill runs`

Many PDFs are visually forms, but they are just drawings on a page: lines, boxes, and printed labels, with no built-in places to type. This file helps turn those static pages into something the system can fill. It has three main jobs. First, the extract command reads a PDF and records useful layout clues: words, long horizontal lines, small square boxes that are likely checkboxes, and row bands between lines. This gives a later process a map of the page. Second, the preview command draws colored rectangles on an image of a page so a person can quickly check whether the planned fill areas line up with the form. Third, the fill command reads a field definition JSON file and places PDF text annotations in the requested boxes. A text annotation is like adding a transparent sticky note containing typed text. The file also checks for common mistakes before writing: text boxes that are too short for the font, or boxes that overlap each other. A key detail is coordinate conversion. Images often count from the top-left corner, while PDFs place annotations using a bottom-left style coordinate system. CoordMapper is the adapter that flips and scales coordinates so text lands in the right place.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: This method converts a rectangle from the coordinate style used by the field data into the rectangle style needed for PDF annotations. It exists because image coordinates and PDF coordinates do not point in the same direction vertically.

**Data flow**: It receives a box as four numbers, plus mapper settings such as PDF width and height, source image size, and coordinate system name. If the source is an image, it scales the box to PDF size and flips the vertical positions. If the source is already PDF-like, it only flips the vertical positions. It returns a four-number annotation rectangle ready for pypdf.

**Call relations**: During the fill flow, the code needs a PDF-ready rectangle before it can create a FreeText annotation. This method acts like the translator between the field JSON’s page map and the PDF writer’s placement rules.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: This function scans one PDF page and collects the visual clues needed to understand where form fields might be. It records text, long horizontal rules, and small square boxes that probably represent checkboxes.

**Data flow**: It receives a pdfplumber page object and a page number. It creates a PageLayout record, inspects line objects to find long horizontal lines, inspects rectangle objects to find checkbox-sized squares, and asks the page for its words. It returns a filled PageLayout for that one page.

**Call relations**: _extract_all_pages calls this once for each page while building a whole-document layout. After this function returns the raw page clues, _extract_all_pages asks _compute_row_ranges to add row bands based on the horizontal lines.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: This function turns horizontal line positions into row ranges. In plain terms, if a form has table-like lines, it marks the spaces between neighboring lines as rows.

**Data flow**: It receives a PageLayout that already contains horizontal rules. It sorts their vertical positions, pairs each line with the next one, and appends row records with a top, bottom, and height. It changes the PageLayout in place and does not return a separate value.

**Call relations**: _extract_all_pages calls this immediately after _extract_page. Together, those two steps turn low-level page objects into a more useful page summary for the extract command.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: This function scans every page in a PDF and builds a layout summary for the whole document. It is the main workhorse behind the extract command.

**Data flow**: It receives a PDF file path. It opens the PDF with pdfplumber, loops through pages in order, extracts each page with _extract_page, adds row ranges with _compute_row_ranges, and collects all PageLayout objects into a list. It returns that list.

**Call relations**: cmd_extract calls this after checking its command-line arguments. This function coordinates the per-page scanning steps and hands the collected page layouts back to cmd_extract for JSON writing.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: This function converts PageLayout objects into plain dictionaries that can be saved as JSON. It strips the data down to simple lists, numbers, and text that other tools can read easily.

**Data flow**: It receives a list of PageLayout records. For each page, it copies the page number, size, text elements, horizontal rules, checkbox candidates, and row ranges into a dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract uses this after _extract_all_pages finishes. The extracted layout is still in Python dataclass objects, and this function prepares it for json.dumps so it can be written to disk.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: This function reads a field-definition JSON file, checks that the requested text placements look safe, and writes text annotations into a new PDF. It protects the output from obvious layout mistakes before saving it.

**Data flow**: It receives an input PDF path, a fields JSON path, and an output PDF path. It reads the JSON, opens the PDF, records each page’s size, then walks through each form field. For fields with text, it checks that the content box is tall enough for the font and that it does not overlap earlier boxes. If validation passes, it converts the box coordinates with CoordMapper, creates a FreeText annotation, adds it to the correct page, and finally writes the finished PDF. If validation fails, it prints errors and exits instead of writing a bad file.

**Call relations**: cmd_fill calls this after command-line argument checking. Inside the fill flow, it uses _rects_overlap to catch colliding boxes, CoordMapper to convert coordinates, and pypdf classes to read the original PDF, create annotations, and write the result.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: This helper answers a simple question: do two rectangles touch or cover the same space? It is used to prevent two pieces of planned text or labels from being placed on top of each other.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges. If one rectangle is completely to the left, right, above, or below the other, they do not overlap; otherwise they do. It returns true or false.

**Call relations**: _validate_and_fill calls this while checking each new field against fields already placed on the same page. Its yes-or-no answer becomes either an overlap error or permission to continue.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: This is the command handler for creating a JSON layout map from a PDF. A user runs it when they need to inspect a non-fillable form and find its text, lines, checkbox-like boxes, and rows.

**Data flow**: It receives the command arguments after the word extract. It expects an input PDF path and an output JSON path. It scans the PDF with _extract_all_pages, converts the result with _pages_to_dict, writes formatted JSON to disk, and prints a summary count. If the arguments are wrong, it prints usage help and exits.

**Call relations**: main dispatches to this function when the user chooses the extract subcommand. This function then drives the extraction helpers and is responsible for turning their in-memory results into a saved JSON file.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: This is the command handler for drawing a visual check of planned field boxes on top of a page image. It helps a person see whether the JSON field coordinates line up before writing into the real PDF.

**Data flow**: It receives four command arguments: page number, fields JSON path, input image path, and output image path. It opens the JSON and image, draws red rectangles for content areas and blue rectangles for label boxes on the requested page, saves the marked-up image, and prints how many fields were highlighted. If the arguments are wrong, it prints usage help and exits.

**Call relations**: main dispatches to this function when the user chooses the preview subcommand. Unlike the fill path, it does not write a PDF; it hands the field coordinates to PIL image drawing tools so the placement can be inspected visually.

*Call graph*: 5 external calls (Draw, open, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: This is the command handler for filling a PDF with text annotations from a field JSON file. It is the user-facing wrapper around the validation and writing step.

**Data flow**: It receives the command arguments after the word fill. It expects an input PDF path, a fields JSON path, and an output PDF path. If the argument count is correct, it passes those paths to _validate_and_fill. If not, it prints usage help and exits.

**Call relations**: main dispatches to this function when the user chooses the fill subcommand. This function keeps command-line checking separate from the deeper fill logic in _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: This is the script’s front door. It reads the command name from the command line and sends the remaining arguments to the right command handler.

**Data flow**: It reads sys.argv, the list of words used to start the program. If no known subcommand is provided, it prints the allowed command names and exits. Otherwise it looks up the chosen subcommand in SUBCOMMANDS and calls the matching function with the rest of the arguments.

**Call relations**: When this file is run directly, Python calls main through the usual __main__ block. main is the dispatcher that routes users to cmd_extract, cmd_preview, or cmd_fill.

*Call graph*: 1 external calls (exit).


### Page image rendering
Command-line helpers for converting PDF pages into PNG images for visual inspection and image-based workflows.

### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command-line document rendering`

A PDF is convenient for people, but many document tools work better with plain image files. This file bridges that gap. Given a PDF path and an output folder, it creates the folder if needed, asks the external pdf2image library to turn the PDF pages into images, then saves one PNG per page.

It also keeps the images from becoming too large. Pages are rendered at a fixed quality level, then any page wider or taller than 1000 pixels is shrunk while keeping its shape. This is like photocopying a page and then reducing it to fit inside a standard frame without stretching it.

The saved files are named in page order, such as page_1.png and page_2.png. As it works, the script prints what it created and the final image size, so a user can see progress and confirm the output. If run from the command line with the wrong number of arguments, it prints a short usage message and exits instead of guessing what to do.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Turns a PDF into a set of PNG files, one image per page. It also limits oversized page images so the output stays practical to store and process.

**Data flow**: It receives the path to an input PDF and the path to an output folder. It creates the folder if it does not already exist, reads the PDF through pdf2image, optionally shrinks each page image to fit within 1000 by 1000 pixels, and writes numbered PNG files into the folder. It returns nothing, but it changes the filesystem by creating image files and prints progress messages.

**Call relations**: This is the worker function that does the actual conversion. The command-line wrapper, main, calls it after checking that the user supplied exactly the PDF path and output directory. Inside, it relies on pathlib.Path to work with folders and on pdf2image.convert_from_path to perform the PDF-to-image conversion.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the script. It checks that the user gave the two required arguments and either starts rendering or prints the correct usage.

**Data flow**: It reads the command-line arguments from sys.argv. If there are not exactly two user-provided values, it prints an example of the expected command and exits with an error code. If the arguments are present, it passes the PDF path and output folder path to render.

**Call relations**: This function is called when the file is run directly as a script. Its job is to guard the front door: it rejects incorrect command-line use with sys.exit, and for valid use it hands the real work to render.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
