# PDF Form, Layout, and Rendering Tools  `stage-12.2.5`

This stage provides small command-line tools for working with PDF documents, especially forms. It is not the main application loop itself. Instead, it is behind-the-scenes support for document workflows that need to inspect, fill, mark up, or view PDFs.

The formfill tool is for true fillable PDFs, called AcroForms. An AcroForm is a PDF that already contains named boxes for typing values, like “name” or “date.” This tool can check whether those fields exist, export the field list as JSON, and fill the PDF using values from JSON.

The layout tool is for PDFs that look like forms but are not actually fillable. It scans the page for layout clues, helps preview where text should be placed, and can write text annotations onto the PDF.

The render tool converts PDF pages into PNG images. These images are easier for other tools, previews, or visual checks to use. Together, the three tools cover the main PDF form cases: detect and fill real fields, place text on flat forms, and turn pages into images for inspection.

## Files in this stage

### Fillable form handling
Tools for detecting AcroForm fields, exporting their structure, and filling PDFs from JSON values.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command invocation`

Many PDFs are just flat pages, but some contain built-in form fields such as text boxes, checkboxes, radio buttons, and dropdowns. This file works with those native PDF fields, rather than drawing text on top of the page. Without it, the rest of the document tooling would not be able to reliably discover or fill official PDF form controls.

The file uses pypdf, a Python library for reading and writing PDFs. First, it can inspect a PDF to see whether fillable fields exist. Then it can extract each field into a plain JSON description: its name, kind, page number, screen position, and possible values for things like checkboxes or dropdowns. Finally, it can read a JSON list of desired field values, check that the values make sense, and write a new filled PDF.

A key detail is that PDFs describe positions using a coordinate system that starts at the bottom of the page, while many layout tools think from the top down. The helper that flips rectangles translates between those views. The file also copes with imperfect PDFs, including “orphaned widgets,” which are visible form controls that are not properly listed in the PDF’s main form directory.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has visible form controls that are not found through the normal form-field list. This matters because some PDFs are built poorly but still contain usable fillable boxes.

**Data flow**: It receives an open PDF reader, looks through each page’s annotations, and searches for widget annotations with a field type. It returns true as soon as it finds one, otherwise false after checking all pages.

**Call relations**: The detect command uses this as a backup check after asking pypdf for normal fields. If the usual field list is empty but this finds widgets, the tool still reports that the PDF is fillable.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a PDF field by walking up its parent chain. This is needed because some PDFs split a field name across parent and child objects, like a folder path made from several folder names.

**Data flow**: It receives one annotation dictionary, reads its own name part, then follows its parent links and gathers any parent name parts. It returns the parts joined with dots, or nothing if no name exists.

**Call relations**: The AcroForm extraction path uses this when matching page annotations back to fields from the PDF’s form directory. That lets the extractor attach page numbers and positions to the right field.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns a raw PDF field dictionary into one of this file’s simpler field objects. It hides the PDF’s short internal type codes behind plain kinds such as text, checkbox, choice, or unknown.

**Data flow**: It receives raw field data and a field name, reads the PDF field type, and chooses the right local data object. Text fields become a basic FormField, button fields are passed to the checkbox builder, choice fields are passed to the choice builder, and unrecognized types become an unknown field.

**Call relations**: Both extraction paths call this when they discover a field. It delegates checkbox and choice details to their specific builders so the rest of the code can work with clearer field objects.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which stored value means checked and which means unchecked. This is important because PDFs do not always use the same label for the checked state.

**Data flow**: It receives raw PDF field data and a name, reads the possible checkbox states, and chooses an on value and an off value. It returns a CheckboxField, printing a warning if the states look unusual.

**Call relations**: _build_field_from_dict calls this for PDF button fields that are treated as checkboxes. Later validation and filling use the on and off values it records.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a choice-style PDF field, such as a dropdown or list. It records both the value the PDF expects and the text a person may see.

**Data flow**: It receives raw field data and a name, reads the available states, and normalizes each option into a small dictionary with value and text. It returns a ChoiceField containing those options.

**Call relations**: _build_field_from_dict calls this when it sees a PDF choice field. The extracted options later help users prepare valid fill values and help validation catch mistakes.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a checkbox’s checked value when the normal field metadata did not provide it. It looks at the checkbox’s appearance states, which are the visual versions the PDF can draw.

**Data flow**: It receives a resolved PDF annotation and an existing CheckboxField. If the checkbox already has an on value, it leaves it alone; otherwise it searches the annotation’s appearance keys for a value other than /Off and writes that into the CheckboxField.

**Call relations**: The widget-based extractor calls this after building a checkbox from page annotations. It improves the field description before that description is returned to the higher-level extraction flow.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from bottom-up page coordinates into top-down coordinates. This makes field positions easier to compare with ordinary page-layout thinking, where the top of the page comes first.

**Data flow**: It receives a rectangle and the page height, converts the rectangle numbers to floats, and recalculates the vertical coordinates by subtracting them from the page height. It returns the converted rectangle.

**Call relations**: The field extractors and radio-option collector call this whenever they record where a form control sits on a page. It keeps all exported rectangles in the same easier-to-use coordinate style.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts form fields directly from page-level widget annotations. This is the fallback path for PDFs whose visible form controls are not properly listed in the main form structure.

**Data flow**: It receives an open PDF reader, visits every page, and checks each annotation. For each widget with a field type and name, it builds a local field object, records its page and rectangle, improves checkbox values when needed, and returns the list of fields.

**Call relations**: _extract_from_acroform calls this when pypdf cannot find normal fields. It uses the field builder, rectangle converter, and checkbox appearance reader to produce the same kind of output as the main extraction path.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the fillable form structure from a PDF’s AcroForm, which is the PDF feature that stores interactive form fields. This is the main discovery step used before exporting or filling fields.

**Data flow**: It receives an open PDF reader, asks pypdf for the form fields, and falls back to widget scanning if none are listed. It builds field objects, finds their page locations by matching annotations, collects radio-button options, skips fields that cannot be located, sorts the result, and returns the final list.

**Call relations**: Both the extract and fill commands depend on this function. It coordinates lower-level helpers: field construction, full-name lookup, rectangle conversion, radio-option collection, and the widget fallback path.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one selectable button option to a radio-button group. A radio group is a set where one choice can be selected, like choosing one answer from several circles on a form.

**Data flow**: It receives a radio annotation, the group name, page information, page height, and the growing collection of radio groups. It finds the annotation’s non-off value, creates the group if needed, converts the option’s rectangle, and appends the option to that group.

**Call relations**: _extract_from_acroform calls this when it sees an annotation that belongs to a radio candidate. The collected options become part of the exported field description and later define which fill values are valid.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a natural ordering for extracted fields: page first, then roughly top-to-bottom, then left-to-right. This makes the JSON output easier for humans to read and edit.

**Data flow**: It receives a field, chooses the field rectangle or the first radio option rectangle, groups nearby vertical positions into rows, and returns a tuple used for sorting. It does not change the field.

**Call relations**: The AcroForm extraction process uses this as the sorting rule before returning fields. It does not call other project functions; it simply gives the final list a predictable order.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts an internal field object into a plain dictionary that can be written as JSON. This turns Python-specific objects into a format people and other tools can read.

**Data flow**: It receives a FormField or one of its specialized versions, copies common details such as name, kind, page, and rectangle, then adds type-specific details like checkbox values, radio options, or choice options. It returns the dictionary.

**Call relations**: The extract command calls this for every discovered field before writing the JSON file. It is the bridge between the extractor’s internal objects and the user-facing output file.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a proposed value is allowed for a checkbox, radio group, or choice field. This prevents writing values that the PDF form would not understand.

**Data flow**: It receives a field description and a string value. For fields with fixed allowed values, it compares the value against those allowed choices and returns an error message if it is invalid; otherwise it returns nothing.

**Call relations**: _validate_fill_entries calls this while checking the user’s fill JSON. Its error messages help the fill command stop before producing a bad or misleading output PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the `detect` command, which tells the user whether a PDF appears to contain native fillable fields. It is a quick yes-or-no check before trying extraction or filling.

**Data flow**: It receives command arguments, verifies that exactly one PDF path was provided, opens the PDF, and checks for normal fields or orphaned widgets. It prints either a fillable-fields message or a suggestion to use manual layout annotation instead.

**Call relations**: The main dispatcher calls this when the user chooses `detect`. It relies on pypdf for normal field discovery and on _has_orphaned_widgets for the fallback check; it exits early if the command was used incorrectly.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the `extract` command, which writes a JSON map of a PDF’s fillable fields. Users can inspect or edit this JSON to understand what values a later fill operation should provide.

**Data flow**: It receives an input PDF path and output JSON path, opens the PDF, extracts field descriptions, converts them to dictionaries, creates the output folder if needed, writes formatted JSON, and prints how many fields were written.

**Call relations**: The main dispatcher calls this for `extract`. It relies on _extract_from_acroform for discovery and _field_to_dict for JSON-ready output, then hands the final text to the filesystem.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the `fill` command, which creates a new PDF with supplied form values written into its native fields. It is the main action command for completing a fillable PDF automatically.

**Data flow**: It receives an input PDF path, a values JSON path, and an output PDF path. It reads the requested values, extracts current field metadata, validates names, pages, and allowed values, groups values by page, writes those values into a cloned PDF, saves the new file, and prints a summary.

**Call relations**: The main dispatcher calls this for `fill`. It uses _extract_from_acroform to know what fields exist, _validate_fill_entries to catch bad input, and pypdf’s writer to update the actual PDF pages.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks the whole user-provided fill list before the PDF is modified. It catches invalid field names, wrong page numbers, and impossible values in one pass.

**Data flow**: It receives a list of value entries and a lookup table of known fields by name. For each entry, it verifies the field exists, confirms the page if one was supplied, asks _validate_fill_value to check the value when present, prints any errors, and returns whether any problem was found.

**Call relations**: cmd_fill calls this before writing the output PDF. It delegates field-type-specific value checks to _validate_fill_value, and its boolean result decides whether filling continues or the command exits.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Acts as the command-line doorway for the file. It chooses which subcommand to run based on the first argument typed by the user.

**Data flow**: It reads the process command-line arguments, checks that a known subcommand was provided, prints a usage message and exits on bad input, or passes the remaining arguments to the selected command function.

**Call relations**: This runs when the file is executed directly with Python. It dispatches to cmd_detect, cmd_extract, or cmd_fill through the subcommand table.

*Call graph*: 1 external calls (exit).


### Non-fillable layout annotation
Tools for discovering visual layout cues in non-fillable PDFs, previewing field placement, and writing text annotations.

### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line PDF extraction, preview, and fill operations`

Many PDFs look like forms but do not contain real fillable fields. This file helps bridge that gap. It treats the PDF page like a paper form on a desk: first it looks for printed words, horizontal divider lines, and small square boxes that might be checkboxes; then it lets another JSON file describe where answers should be placed; finally it writes those answers onto the PDF as text annotations.

The file has three user-facing commands. The extract command opens a PDF and records page layout information as JSON, including words, long horizontal rules, likely checkbox boxes, and row ranges between rules. This gives a later process enough landmarks to decide where form fields are. The preview command draws red and blue rectangles over an image of a page, so a person can visually check whether the planned answer boxes line up. The fill command reads the planned fields, checks for common mistakes such as overlapping boxes or a text box too short for its font size, converts coordinates into PDF annotation coordinates, and writes the text into a new PDF.

A key detail is coordinate conversion. Images usually count y positions downward from the top, while PDF annotations use a coordinate system based from the bottom. CoordMapper is the translator between those worlds.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: This method turns a rectangle from the field description into the rectangle format that a PDF annotation needs. It is especially important when the field locations were drawn on an image rather than measured directly in PDF coordinates.

**Data flow**: It receives a box as four numbers: left, top, right, and bottom. It reads the mapper's PDF size, source size, and coordinate-system setting. If the source is an image, it scales the box from image size to PDF size and flips the vertical direction; otherwise it only flips the vertical direction. It returns a four-number rectangle ready to pass to the PDF annotation writer.

**Call relations**: During the fill flow, _validate_and_fill creates a CoordMapper for each field's page and uses this method before adding text to the PDF. The method is the last translation step between the human-planned field area and the rectangle that pypdf can place on the page.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: This function scans one PDF page and records the layout clues that are useful for understanding a form. It looks for long horizontal lines, small square boxes that are likely checkboxes, and all extracted words.

**Data flow**: It receives a pdfplumber page object and a page number. It creates a PageLayout record, reads drawing objects and words from the page, filters lines and rectangles using simple size rules, rounds their positions, and stores them in lists. It returns the filled PageLayout for that page.

**Call relations**: _extract_all_pages calls this once for every page in the PDF. It depends on pdfplumber's page data and word extraction, then hands the raw page layout onward so _compute_row_ranges can add row boundaries before the extract command writes everything to JSON.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: This function turns detected horizontal lines into vertical row bands. Those row bands help describe table-like form sections, where each row sits between two printed rules.

**Data flow**: It receives a PageLayout that already contains horizontal rule positions. It sorts the unique y positions of those rules, then creates one row range between each neighboring pair. It changes the PageLayout in place by adding those row ranges; it does not return a separate value.

**Call relations**: _extract_all_pages calls this right after _extract_page. In the bigger extraction flow, _extract_page gathers the raw lines, and this function interprets those lines as row boundaries before the data is saved.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: This function scans an entire PDF and builds layout records for every page. It is the main worker behind the extract command.

**Data flow**: It receives a PDF file path. It opens the PDF with pdfplumber, loops through the pages in order, extracts each page's layout, computes row ranges for that page, and collects all PageLayout objects into a list. It returns that list.

**Call relations**: cmd_extract calls this after checking the command-line arguments. Inside, it delegates page-level scanning to _extract_page and row calculation to _compute_row_ranges, then gives the completed page list back to cmd_extract for JSON output.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: This function converts PageLayout objects into plain dictionaries that can be written as JSON. It strips away the Python dataclass wrapper and leaves simple data.

**Data flow**: It receives a list of PageLayout objects. For each page, it copies the page number, size, words, horizontal rules, checkbox candidates, and row ranges into a dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after _extract_all_pages has scanned the PDF. Its output is passed to json.dumps so the layout can be saved in a portable file that other tools or people can read.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: This function reads a field-definition JSON file, checks whether the planned fields look safe to place, and writes the requested text onto a new PDF. It prevents obvious bad output, such as overlapping fields or text boxes too short for the chosen font.

**Data flow**: It receives paths for the input PDF, the JSON field file, and the output PDF. It reads the JSON, opens the PDF for copying, records each page's real size, and then walks through the form fields. For each field with text, it checks height and overlap, converts the field rectangle into PDF annotation coordinates, creates a FreeText annotation, and adds it to the correct page. If validation errors are found, it prints them and exits without writing the result; otherwise it creates the output folder if needed and writes the new PDF.

**Call relations**: cmd_fill calls this after validating the fill command's arguments. Inside the fill process, it uses _rects_overlap to catch collisions, creates a CoordMapper to translate coordinates, and hands the final annotation objects to pypdf's writer. It is the central worker for producing the finished filled PDF.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: This small helper answers a simple question: do two rectangles cover any of the same area? It is used to catch field placements that would print on top of each other.

**Data flow**: It receives two rectangles, each represented by left, top, right, and bottom numbers. It compares their edges. It returns true if they overlap and false if one is completely to the left, right, above, or below the other.

**Call relations**: _validate_and_fill calls this while checking each new field against rectangles already placed on the same page. Its yes-or-no answer becomes either a validation error or permission to continue placing the annotation.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: This is the command-line entry for scanning a PDF layout into JSON. A user runs it when they need a machine-readable map of the PDF's visible form structure.

**Data flow**: It receives the command arguments after the word extract. It expects an input PDF path and an output JSON path. It scans the PDF, converts the page layouts into dictionaries, writes formatted JSON to disk, and prints a short summary of how many items it found. If the arguments are wrong, it prints usage text and exits.

**Call relations**: main calls this when the user chooses the extract subcommand. It then calls _extract_all_pages to do the PDF scanning and _pages_to_dict to prepare the result for json.dumps before saving the file.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: This is the command-line entry for drawing a visual check of planned form fields on top of a page image. It helps people spot bad coordinates before writing anything into a PDF.

**Data flow**: It receives the command arguments after the word preview: page number, fields JSON, input image, and output image. It reads the field definitions, opens the image, draws red rectangles for content areas and blue rectangles for label boxes on the requested page, saves the new image, and prints how many fields were highlighted. If the arguments are wrong, it prints usage text and exits.

**Call relations**: main calls this when the user chooses the preview subcommand. It does not call the PDF extraction or filling functions; instead it uses Pillow image tools directly to create a visual proof of the field layout.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: This is the command-line entry for creating a filled copy of a non-fillable PDF. It is the user-facing wrapper around the validation and annotation-writing work.

**Data flow**: It receives the command arguments after the word fill. It expects an input PDF path, a fields JSON path, and an output PDF path. If the arguments are correct, it passes those paths to _validate_and_fill; if not, it prints usage text and exits.

**Call relations**: main calls this when the user chooses the fill subcommand. It keeps the command-line parsing simple and hands the real work to _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: This is the script's top-level dispatcher. It decides which command the user asked for and sends the remaining arguments to the right command function.

**Data flow**: It reads sys.argv, the list of words typed on the command line. If no valid subcommand was given, it prints the allowed usage and exits. Otherwise it looks up the selected command in SUBCOMMANDS and calls it with the rest of the arguments.

**Call relations**: When this file is run directly as a script, main starts the flow. It routes to cmd_extract, cmd_preview, or cmd_fill, which then carry out the requested PDF task.

*Call graph*: 1 external calls (exit).


### Page image rendering
Tools for converting PDF pages into PNG images for inspection, display, or downstream document workflows.

### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command run / PDF preprocessing`

PDF files are good for reading, but many automated tools work better with images. This file bridges that gap: it opens a PDF, renders every page as a picture, shrinks overly large pictures to a safe maximum size, and writes the result into an output folder.

The main work happens in `render`. It first makes sure the destination folder exists. Then it asks `pdf2image` to convert the PDF pages into image objects at 200 DPI, meaning a reasonably detailed image resolution. For each page, it checks the width and height. If either side is larger than 1000 pixels, it scales the image down while keeping the same shape, like resizing a photo without stretching it. Each page is saved as `page_1.png`, `page_2.png`, and so on, and the script prints a short progress message.

The `main` function makes the file usable from a terminal. It expects exactly two arguments: the input PDF path and the output directory. If they are missing, it prints a usage hint and stops. Without this file, someone would need to manually convert PDF pages into images before the rest of the document workflow could use them.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts a PDF into one PNG image per page. It also keeps the saved images from being too large by shrinking any page image whose width or height is over the configured maximum.

**Data flow**: It takes a path to a PDF and a path to an output folder. It creates the folder if needed, reads the PDF through the external `pdf2image` library, resizes any oversized page images, then writes PNG files named by page number into the folder. Its visible output is the set of image files on disk plus progress messages printed to the terminal.

**Call relations**: This is the worker function used by `main` after the command-line arguments have been checked. It relies on `pdf2image.convert_from_path` to do the hard part of reading and rendering the PDF, and on `pathlib.Path` to create and build filesystem paths safely.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Provides the command-line front door for the script. It checks that the user supplied the input PDF and output folder, then starts the rendering work.

**Data flow**: It reads the command-line arguments from `sys.argv`. If there are not exactly two user-provided values, it prints the correct usage pattern and exits with an error code. If the arguments are present, it passes them to `render`, which creates the image files.

**Call relations**: This function runs when the file is executed directly as a script. It acts like a receptionist: it verifies the request is complete, stops early with `sys.exit` if it is not, and otherwise hands the real conversion job to `render`.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
