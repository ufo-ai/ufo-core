# PDF rendering, layout, and form filling tools  `stage-10.2.5`

This stage provides small command-line tools for working with PDFs when the system needs to see, inspect, or modify document pages. It is not part of startup or shutdown. It is behind-the-scenes support used during document processing, especially when a PDF must be previewed, analyzed, or filled in.

The render tool turns each PDF page into a PNG image. This is like taking a clear photo of every page, so other parts of the system can preview it or use image-based checks.

The layout tool helps with PDFs that are just flat pages, with no real fillable boxes inside them. It inspects the visual layout, shows where form-like fields might be placed, and can write text annotations onto the page. This makes a plain document easier to understand and safely mark up.

The formfill tool works with PDFs that already contain native form fields. It can detect those fields, export their names and values as JSON, and fill them back in from JSON data. Together, these tools cover both image-style PDFs and true fillable forms.

## Files in this stage

### Form field workflows
Command-line support for detecting native PDF form fields, exporting them to JSON, and filling forms from JSON values.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command execution`

PDFs can contain real form fields, such as text boxes, checkboxes, radio buttons, and dropdown choices. These are different from drawing text on top of a page: they are built into the PDF as named fields. This file gives the project a native way to inspect and fill those fields using pypdf, a Python library for reading and writing PDFs.

The tool has three commands. The detect command checks whether a PDF appears to contain fillable fields. The extract command reads the PDF and writes a JSON description of each field, including its name, type, page number, and position. The fill command reads a JSON file of desired values, checks that the names, pages, and allowed values make sense, and then writes a new filled PDF.

A key complication is that PDFs do not all describe forms in the same clean way. Some fields live in the main AcroForm structure, which is the PDF’s normal form table. Others appear only as page annotations, like loose sticky notes attached to a page. This file supports both. It also translates PDF coordinates into a more familiar top-down page position, and it treats checkboxes, radio groups, and choice fields carefully because their valid values are often special PDF names rather than plain words.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has standalone form widgets even if they are not listed in the normal form table. This matters because some PDFs still have usable fields even when the main form directory looks empty.

**Data flow**: It receives a PDF reader, looks through every page, then looks through that page’s annotations. If it finds an annotation marked as a widget with a field type, it returns true; otherwise it returns false after checking all pages.

**Call relations**: The detect command calls this as a fallback check. First it asks pypdf for normal form fields; if that does not find any, this helper looks for loose widget annotations before the tool says the PDF has no fillable fields.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a field annotation by walking up through its parent fields. PDF forms can nest names, so this turns pieces like a family name path into one dotted name.

**Data flow**: It receives one annotation dictionary, reads its own name part, then follows its parent links and collects their name parts too. It returns the parts in parent-to-child order joined with dots, or nothing if no name is found.

**Call relations**: The AcroForm extraction path uses this while scanning page annotations. It needs the full name so a visible widget on a page can be matched back to the field metadata found earlier.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field data into one of this file’s simpler field objects. It hides PDF field codes behind everyday kinds such as text, checkbox, choice, or unknown.

**Data flow**: It receives a raw field dictionary and the field’s name. It reads the PDF field type code, then creates a matching field object: plain text fields become FormField objects, button fields are passed to the checkbox builder, choice fields are passed to the choice builder, and unfamiliar types become an unknown field record.

**Call relations**: Both extraction routes call this when they discover a field. It delegates the special cases to _build_checkbox and _build_choice, because those field types need extra information beyond just a name and kind.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox description and tries to identify which PDF value means checked and which means unchecked. This is important because PDFs often use special names, not true or false.

**Data flow**: It receives raw checkbox-like PDF data and a name. It reads the available state values, chooses an on value and an off value when possible, prints a warning if the states look unusual, and returns a CheckboxField object.

**Call relations**: _build_field_from_dict calls this whenever it sees a PDF button field. The resulting object is later used during extraction output and during fill validation so users know which values are legal.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description of a dropdown or list-style field and records its allowed choices. This lets extracted JSON show both the stored value and the human-facing text where available.

**Data flow**: It receives raw choice-field PDF data and a name. It reads the available states, turns each one into a small value/text record, and returns a ChoiceField object containing those options.

**Call relations**: _build_field_from_dict calls this for PDF choice fields. The filled-output path later uses the stored choices to reject values that are not valid for that field.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a checkbox’s checked value when the earlier field data did not provide it. It looks at the checkbox’s appearance states, which often reveal the real checked value.

**Data flow**: It receives a resolved PDF annotation and a CheckboxField object. If the checkbox already has an on value, it does nothing. Otherwise it looks inside the annotation’s normal appearance entries, picks the first key that is not /Off, and writes that into the CheckboxField.

**Call relations**: _extract_from_widgets calls this while reading loose widget annotations. It improves checkbox metadata before the field is added to the extracted list.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle into a top-down coordinate style that is easier for people and many layout tools to understand. PDFs usually measure from the bottom of the page, while people often think from the top.

**Data flow**: It receives a rectangle and the page height. It reads the rectangle’s left, bottom, right, and top values, then returns a new rectangle where the vertical coordinates are flipped relative to the page height.

**Call relations**: The extraction helpers call this whenever they record where a field appears on a page. Radio option collection uses it too, so all exported positions follow the same coordinate convention.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts form fields directly from page widget annotations. This is the fallback path for PDFs whose fields are present on pages but missing from the main form table.

**Data flow**: It receives a PDF reader, walks through every page, and checks each annotation. For each widget annotation with a field type and name, it builds a field object, records the page number, converts its rectangle, improves checkbox values when needed, and returns the collected list.

**Call relations**: _extract_from_acroform calls this when pypdf does not find normal AcroForm fields. Inside, it relies on _build_field_from_dict, _flip_rect, and _extract_checkbox_on_value to turn low-level PDF entries into useful field descriptions.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the fillable fields from a PDF’s normal AcroForm structure and attaches page locations to them. It is the main discovery routine used before exporting field metadata or filling values.

**Data flow**: It receives a PDF reader and asks pypdf for the PDF’s fields. If none are found, it falls back to widget extraction. Otherwise it builds field objects from the raw field table, tracks radio-button candidates separately, scans page annotations to find each field’s page and rectangle, collects radio options, warns about fields that cannot be located, sorts the final list, and returns it.

**Call relations**: cmd_extract calls this to produce the JSON field list. cmd_fill calls it to learn what fields exist before validating and writing values. It coordinates several helpers: _build_field_from_dict for field objects, _full_field_name for matching annotations, _flip_rect for positions, _collect_radio_option for radio groups, and _extract_from_widgets as a fallback.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one visible radio-button choice to its radio group description. A radio group is one field with several possible buttons, so each button must be collected as an option.

**Data flow**: It receives an annotation, the group name, page index, page height, and the current radio-group dictionary. It reads the annotation’s appearance keys to find the one checked value, creates the group if needed, converts the option rectangle, and appends a value-and-position record to that group.

**Call relations**: _extract_from_acroform calls this while scanning page annotations that belong to radio-button candidates. It hands completed radio group information back through the shared radio_groups dictionary.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a consistent reading order for extracted fields. It sorts fields by page, then roughly by row, then by left-to-right position.

**Data flow**: It receives a field object. For normal fields it uses the field rectangle; for radio groups it uses the first option rectangle. It rounds the vertical position into row-sized bands and returns a sortable tuple of page, row, and horizontal position.

**Call relations**: The AcroForm extraction flow uses this when ordering the final field list. That makes the JSON output easier to read because nearby fields appear together instead of in whatever internal order the PDF happened to use.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Turns an internal field object into plain dictionary data that can be written as JSON. This is the bridge between Python objects and the extracted metadata file.

**Data flow**: It receives a field object and starts with its name and kind. It adds page and rectangle when available, then adds type-specific details such as checkbox on/off values, radio options, or choice options. It returns a dictionary ready for JSON serialization.

**Call relations**: cmd_extract calls this for every extracted field. The command then writes the resulting list of dictionaries to the requested JSON file.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a requested value is allowed for a particular field. It prevents writing impossible checkbox, radio, or choice values into the PDF.

**Data flow**: It receives a field object and a proposed string value. For checkboxes it compares the value with the known on and off values. For radio groups and choice fields it compares the value with the available options. It returns an error message if the value is invalid, or nothing if it is acceptable.

**Call relations**: _validate_fill_entries calls this for entries that include a value. Its error message is printed before cmd_fill stops, so the user can fix the JSON instead of getting a silently wrong PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the detect subcommand. It tells the user whether a PDF appears to contain native fillable fields.

**Data flow**: It receives the command arguments. If the argument count is wrong, it prints usage text and exits. Otherwise it opens the PDF, checks for normal fields and orphaned widgets, then prints either a success message or a note suggesting manual layout-based annotation instead.

**Call relations**: main dispatches to this when the user runs the detect command. It uses _has_orphaned_widgets as a second check after pypdf’s normal field lookup.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract subcommand. It writes a JSON map of the PDF’s fillable fields so a person or another tool can see what can be filled.

**Data flow**: It receives an input PDF path and an output JSON path. If the arguments are wrong, it prints usage text and exits. Otherwise it opens the PDF, extracts fields, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written.

**Call relations**: main dispatches to this for the extract command. It depends on _extract_from_acroform for discovery and _field_to_dict for JSON-friendly output.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill subcommand. It reads requested field values from JSON and writes a new PDF with those values applied.

**Data flow**: It receives an input PDF path, a values JSON path, and an output PDF path. It loads the requested values, extracts the PDF’s real field metadata, validates the requested entries, groups values by page, clones the PDF into a writer, updates each page’s form fields, creates the output folder if needed, writes the new PDF, and prints a summary.

**Call relations**: main dispatches to this for the fill command. It calls _extract_from_acroform to learn the PDF’s fields and _validate_fill_entries to stop bad input before handing valid page-level values to pypdf’s writer.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks the whole fill-values JSON list before any PDF is written. It catches unknown field names, wrong page numbers, and invalid field values.

**Data flow**: It receives the list of requested fill entries and a lookup table of real fields by name. For each entry, it checks that the name exists, that any supplied page matches the field’s actual page, and that any supplied value is valid for that field type. It prints errors as it finds them and returns true if any error occurred.

**Call relations**: cmd_fill calls this after extracting field metadata and before updating the PDF. For value-specific checks, it delegates to _validate_fill_value.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Chooses which command to run when this file is executed as a script. It is the front door for the command-line interface.

**Data flow**: It reads the process command-line arguments. If no valid subcommand is provided, it prints the general usage message and exits. Otherwise it looks up the matching command function and passes along the remaining arguments.

**Call relations**: Python calls this when the file is run directly. It dispatches to cmd_detect, cmd_extract, or cmd_fill through the SUBCOMMANDS table.

*Call graph*: 1 external calls (exit).


### Visual layout markup
Tools for inspecting PDF page layout, previewing form-like regions, and adding text annotations to flat PDFs.

### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line execution for PDF extraction, preview, and filling`

Many PDFs look like forms but do not contain real fillable fields. To a computer, they are just pages with text, lines, boxes, and coordinates. This file bridges that gap. It can scan a PDF and describe useful page features, such as words, long horizontal rules, checkbox-sized rectangles, and row bands between rules. That extracted layout can then guide a human or another tool in defining where answers should be placed.

It also provides two practical follow-up steps. The preview command draws the chosen content areas on an image of a page, like placing colored tape over a paper form to check that the target boxes are right. The fill command reads those field definitions, checks for common mistakes such as overlapping boxes or text areas too short for their font, converts coordinates into PDF annotation coordinates, and writes visible text annotations into a new PDF.

One important detail is coordinate conversion. Images and PDFs often count vertical position differently: image coordinates usually start at the top, while PDF annotation coordinates are based from the bottom. CoordMapper performs that flip and optional scaling so text lands where the field definition intended.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: Converts a rectangle from the field-definition coordinate system into the rectangle format expected by PDF annotations. This matters because an area that looks correct on an image or extracted layout can land in the wrong vertical position unless the coordinates are translated.

**Data flow**: It receives a box as four numbers: left, top, right, and bottom. If the source coordinates came from an image, it first scales them to the PDF page size, then flips the vertical direction so the PDF library can use them. If the source is already PDF-sized, it only performs the vertical flip. It returns a four-number rectangle ready to give to the annotation writer.

**Call relations**: During the fill command, _validate_and_fill creates a CoordMapper for the page being written and asks this method to translate each content area before creating the PDF text annotation.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: Reads one PDF page and pulls out the visual clues that are useful for understanding a form-like layout. It records page size, words, long horizontal lines, and small square boxes that probably represent checkboxes.

**Data flow**: It receives a pdfplumber page object and a page number. It creates a PageLayout, scans the page’s line objects for long horizontal rules, scans rectangle objects for checkbox-sized shapes, and asks the page for its words. It returns a filled PageLayout object containing rounded coordinates and detected layout features.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. It relies on pdfplumber’s page data, including extract_words, then hands the raw page layout back so row ranges can be computed next.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: Finds row bands between horizontal rules on a page. This is useful for forms or tables where long horizontal lines divide the page into rows.

**Data flow**: It receives a PageLayout that already contains detected horizontal rules. It sorts the vertical positions of those rules, then creates a row range for each neighboring pair, including the top, bottom, and height. It changes the PageLayout in place by adding those row ranges.

**Call relations**: _extract_all_pages calls this immediately after _extract_page. Together, those two steps turn raw page objects into a more helpful layout summary.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: Scans an entire PDF and builds a layout summary for every page. This is the main work behind the extract command.

**Data flow**: It receives the path to a PDF file. It opens the PDF with pdfplumber, visits each page in order, extracts that page’s visual elements, computes row ranges, and collects the results. It returns a list of PageLayout objects, one per page.

**Call relations**: cmd_extract calls this when the user asks to scan a PDF. Inside the scan, it delegates page-level work to _extract_page and row detection to _compute_row_ranges.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: Turns PageLayout objects into plain dictionaries that can be written as JSON. This makes the extracted layout easy for people and other tools to read.

**Data flow**: It receives a list of PageLayout objects. For each page, it copies the page number, size, text elements, horizontal rules, checkbox candidates, and row ranges into a regular dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract uses this after _extract_all_pages finishes. The result is then serialized to JSON for the output file.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: Reads field definitions, checks that they are safe and plausible, then writes text annotations into a copy of the PDF. It is the core of the fill operation.

**Data flow**: It receives an input PDF path, a JSON field-definition path, and an output PDF path. It reads the JSON, opens the PDF, records each page’s size, then walks through the requested form fields. For each field with text, it checks whether the content box is tall enough and whether it overlaps earlier content or label boxes. If there are serious problems, it prints errors and stops. Otherwise, it converts the content area into PDF annotation coordinates, creates a FreeText annotation, adds it to the right page, writes the new PDF, and prints how many boxes were placed.

**Call relations**: cmd_fill hands user arguments to this function. It uses _rects_overlap to catch collisions, CoordMapper.to_annotation_rect to translate coordinates, and pypdf’s reader, writer, and FreeText annotation tools to produce the final PDF.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: Checks whether two rectangular areas touch or cover the same space. This prevents newly placed text or labels from accidentally sitting on top of each other.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom coordinates. It compares their edges to see whether one is completely to the side of or above the other. It returns true when the rectangles overlap and false when they do not.

**Call relations**: _validate_and_fill calls this while reviewing fields that are about to be placed on the same page. Its answer becomes part of the validation step before the PDF is written.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract subcommand. It scans a PDF and saves a JSON description of the page layout.

**Data flow**: It receives command-line arguments after the word extract. If the user did not provide an input PDF and output JSON path, it prints the correct usage and exits. Otherwise, it scans all pages, converts the results to JSON-friendly dictionaries, creates the output folder if needed, writes the JSON file, and prints a short summary of what was found.

**Call relations**: main calls this when the first command-line word is extract. It relies on _extract_all_pages for the actual PDF scan and _pages_to_dict for preparing the data to write.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: Implements the preview subcommand. It draws the planned field boxes onto an image so a person can visually check whether the chosen areas line up with the form.

**Data flow**: It receives command-line arguments after the word preview: page number, field JSON, input image, and output image. It loads the field definitions and image, draws red rectangles around content areas for the selected page, draws blue rectangles around label boxes when present, saves the marked-up image, and reports how many fields were highlighted.

**Call relations**: main calls this when the user chooses preview. Unlike the PDF-filling path, this command does not write annotations; it uses Pillow image tools to create a visual check before filling.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill subcommand. It validates the requested fields and writes them as visible text annotations into a new PDF.

**Data flow**: It receives command-line arguments after the word fill. If the user did not provide an input PDF, field JSON, and output PDF, it prints the correct usage and exits. With valid arguments, it passes the three paths to _validate_and_fill, which does the checking and writing.

**Call relations**: main calls this when the user chooses fill. It is a thin command wrapper around _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: Chooses which command to run based on the command-line input. It is the entry point when this file is run as a script.

**Data flow**: It reads sys.argv, the list of words the user typed in the terminal. If there is no recognized subcommand, it prints a usage message and exits. Otherwise, it looks up the matching command function and passes along the remaining arguments.

**Call relations**: When Python runs this file directly, the bottom of the file calls main. main then dispatches to cmd_extract, cmd_preview, or cmd_fill depending on the requested subcommand.

*Call graph*: 1 external calls (exit).


### Page image rendering
Rendering utilities that convert PDF pages into PNG images for previewing, inspection, or image-based processing.

### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `command execution`

A PDF is a document format, but many tools work more easily with images. This file acts like a simple photocopier: it opens a PDF, makes one picture for each page, and saves those pictures into a folder. Without it, anything that expects separate page images would first need another way to convert the PDF.

The main work happens in `render`. It creates the output folder if it does not already exist, asks the `pdf2image` library to convert the PDF pages into images, then walks through those images one by one. Each page is rendered at 200 DPI, meaning a reasonably detailed image resolution. If a page image is wider or taller than 1000 pixels, it is scaled down while keeping the same shape, so very large pages do not create oversized files. Each result is saved as `page_1.png`, `page_2.png`, and so on.

The `main` function makes this usable from a terminal. It checks that the user provided exactly two arguments: the input PDF path and the output folder. If the arguments are missing or wrong, it prints a usage message and exits. If they are correct, it passes them to `render`.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts a PDF file into one PNG image per page and writes those images into a chosen folder. It also keeps images from becoming too large by shrinking any page image whose width or height is over the configured limit.

**Data flow**: It receives a PDF file path and an output directory path. It creates the output directory if needed, reads the PDF through `pdf2image.convert_from_path`, resizes any oversized page images, saves each page as a numbered PNG file, and prints progress messages. The result is a folder containing image files such as `page_1.png`, with no returned value.

**Call relations**: This is the worker function called by `main` after the command-line arguments have been checked. It hands the PDF reading and initial page conversion to the external `pdf2image` library, then takes responsibility for resizing, naming, saving, and reporting each output image.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the script. It makes sure the user supplied the required input PDF and output folder before starting the rendering work.

**Data flow**: It reads the command-line arguments from `sys.argv`. If there are not exactly two user-provided arguments, it prints the expected usage and exits with an error code. If the arguments are present, it passes the PDF path and output directory to `render`, which produces the image files.

**Call relations**: This function is run when the file is executed directly as a script. It sits in front of `render` like a receptionist: it checks that the request has the needed information, then sends the actual conversion job to `render`; if the request is incomplete, it calls `sys.exit` to stop the program.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
