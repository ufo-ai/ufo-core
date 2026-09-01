# Document Rendering and PDF Form/Layout Utilities  `stage-10.3.6`

This stage is shared document support. It sits behind the scenes when the system needs to look at a document, show its pages, or prepare a PDF form for automated use. The document renderer is the safety gate. It sends a document to a separate rendering service, asks for only a limited number of page images and text, and checks that the returned images are valid. This keeps oversized, damaged, or strange files from disrupting the rest of the system.

The PDF tools are small command-line helpers, meaning they can be run as standalone programs. The render tool converts PDF pages into PNG images, like taking a clear picture of each page. The form-fill tool works with PDFs that already contain fillable fields: it can find the fields, save their names and positions to JSON, and fill them using JSON values. The layout tool helps with PDFs that are not truly fillable. It inspects page layout, draws preview boxes, and places text at chosen spots so answers appear in the right places.

## Files in this stage

### Safe Document Rendering
Core rendering support converts documents into bounded page images and extracted text while isolating malformed or oversized inputs.

### `core/src/ufo/harness/document_renderer.py`

`io_transport` · `request handling`

This file is the bridge between raw document bytes, such as a PDF or office file, and something the rest of the tool can inspect page by page: text plus PNG images. Without it, callers would either need to understand every document format themselves or risk accepting oversized, corrupt, or misleading render output.

The main class, DocumentRenderer, sends the document to an external service called ufo-preview. It asks for only a bounded page range, with fixed image size limits, so one large document cannot overwhelm memory or produce too much output. The service returns a ZIP bundle, which is like a small package containing a manifest file and one PNG image per rendered page.

The file is careful about trust. It checks the input document size before sending it. It checks the HTTP response from the service. It limits the returned ZIP bundle size while downloading. Then it opens the bundle and verifies that the manifest says exactly what was requested, that page numbers are sensible and consecutive, that no unexpected files are present, and that each page image is really a PNG and within size limits.

The final result is a plain dictionary containing the original path, document type, extracted text, page count, next-page marker, base64-encoded page images, and a reminder to check visual quality. Base64 is a text-safe way to carry binary image data through systems that expect text.

#### Function details

##### `DocumentRenderer.render`  (lines 60–107)

```
async def render(self, path: str, kind: str, content: bytes, start_page: int, limit: int) -> dict[str, object]
```

**Purpose**: This asynchronous function sends a document to the preview rendering service and returns a cleaned-up page-by-page result. It is the public entry point for turning document bytes into text and images while enforcing size and page-count limits.

**Data flow**: It starts with a file path, document kind, raw document bytes, and the requested first page and page count. It first rejects documents that are too large, then normalizes the requested page range so it starts at page 1 or later and never asks for more than the allowed maximum. It builds a small JSON request, posts that plus the file bytes to the rendering service, and reads the returned ZIP bundle in chunks while stopping if the bundle grows too large. If the service reports an error, it reads a short error message and raises a clear exception. If the download succeeds, it passes the bundle to _unpack in a background thread and returns the dictionary that _unpack creates.

**Call relations**: This is the outward-facing method callers use when they need a rendered document. Inside, it uses json.dumps to prepare the render instructions, httpx.Timeout and httpx.AsyncClient to make the HTTP request, and asyncio.to_thread to hand the CPU and disk-style ZIP unpacking work to _unpack without blocking the async event loop.

*Call graph*: 4 external calls (to_thread, AsyncClient, Timeout, dumps).


##### `DocumentRenderer._unpack`  (lines 109–196)

```
def _unpack(self, path: str, kind: str, start_page: int, limit: int, bundle: bytes) -> dict[str, object]
```

**Purpose**: This function opens and verifies the ZIP bundle returned by the rendering service, then converts its page images and text into the final result format. It exists as a safety checkpoint so the system does not blindly trust service output.

**Data flow**: It receives the original path and document kind, the requested page range, and the raw ZIP bundle bytes. It opens the bytes as a ZIP archive, reads manifest.json, and validates the manifest against the expected document kind, page range, page count, and page ordering. It also checks that the archive contains only the manifest and the expected page image files. For each page, it confirms the image name, size, total image budget, and PNG file signature, then reads the image, base64-encodes it into text, and stores it with page number and dimensions. It also gathers any extracted page text. It returns a dictionary with document metadata, combined text, encoded page images, pagination information, and a quality reminder.

**Call relations**: This function is called by DocumentRenderer.render after the remote service has returned a bundle. It uses io.BytesIO to treat the downloaded bytes like a file, zipfile.ZipFile to inspect the ZIP package, and base64.b64encode to turn each binary PNG image into text-safe data for the final response.

*Call graph*: 3 external calls (b64encode, BytesIO, ZipFile).


### Fillable Form Utilities
Command-line tooling detects PDF form fields, exports their structure, and fills them from JSON data.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command-line PDF form processing`

Fillable PDFs can store real form fields inside the file, not just visible text on a page. This file gives the project a native way to inspect and fill those fields using pypdf, a Python library for reading and writing PDF files. Without it, the system would have to guess field positions visually or place text manually, which is less reliable for true PDF forms.

The file works like a three-mode tool. In detect mode, it checks whether a PDF contains fillable fields. In extract mode, it reads those fields and writes a JSON description: field name, type, page, position, and allowed choices where relevant. In fill mode, it reads a JSON list of desired values, checks that those values make sense for the discovered fields, and writes a new filled PDF.

The code has to deal with two ways PDFs describe forms. A well-structured PDF has an AcroForm, which is the PDF form directory. Some PDFs instead have “widget” annotations, which are clickable form controls sitting on pages but not neatly listed in the form directory. The file supports both. It also normalizes PDF coordinates into a more page-layout-friendly form, because PDF coordinates start from the bottom of the page while many layout tools think from the top.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has form-like controls on its pages even when they are not listed in the normal PDF form directory. This matters because some PDFs contain usable fields in a less organized form.

**Data flow**: It receives a PDF reader, looks through every page, then through each page annotation. If it finds a widget annotation with a field type, it returns true; otherwise it returns false after checking the whole document.

**Call relations**: The detect command calls this after asking pypdf for normal form fields. It is the fallback check that lets detection still work for PDFs whose fields are only attached directly to pages.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a PDF field when the name is split across a child field and its parent fields. This turns nested PDF form naming into one readable dotted name.

**Data flow**: It receives one annotation dictionary, walks upward through its parent chain, collects each name part it finds, reverses them into parent-to-child order, and returns a joined name such as `parent.child`. If there are no name parts, it returns nothing.

**Call relations**: Field extraction from the AcroForm uses this while scanning page annotations. It helps connect a visible widget on a page back to the field metadata that was found earlier.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field data into one of this file’s simpler field objects. It hides PDF field codes behind plain kinds like text, checkbox, choice, or unknown.

**Data flow**: It receives a raw PDF field dictionary and a field name. It reads the PDF field type code, then creates a text field directly, hands button fields to the checkbox builder, hands choice fields to the choice builder, or creates an unknown field if the type is not recognized.

**Call relations**: Both main extraction paths use this whenever they find a raw field. It delegates special cases to `_build_checkbox` and `_build_choice` so the rest of the code can work with clearer field objects.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which stored value means checked and which means unchecked. This is needed because PDFs often use custom names for the checked state.

**Data flow**: It receives raw checkbox data and the field name. It reads the field’s possible states, chooses `/Off` as the unchecked value when present, warns if the two states look non-standard, and returns a checkbox object with on and off values filled in as well as possible.

**Call relations**: `_build_field_from_dict` calls this when it sees a PDF button field. The resulting checkbox metadata is later used during extraction output and during fill-value validation.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a drop-down or choice-style field, including the allowed options. This lets users know what values they may safely put into the fill JSON.

**Data flow**: It receives raw choice-field data and the field name. It reads the available states, converts each option into a simple value-and-text dictionary, and returns a choice field object containing that list.

**Call relations**: `_build_field_from_dict` calls this when it sees a PDF choice field. The extracted choices later appear in the JSON map and are used to reject invalid fill values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a missing checkbox checked value by looking at the checkbox’s visual appearance states. This helps with PDFs where the main field metadata does not clearly say what value means checked.

**Data flow**: It receives a resolved PDF annotation and an existing checkbox object. If the checkbox already has an on value, it leaves it alone. Otherwise it looks under the annotation’s appearance data, finds the first state that is not `/Off`, and stores that as the checkbox’s on value.

**Call relations**: The widget-based extraction path calls this after creating a checkbox from a page annotation. It is a cleanup step that makes later filling and validation more accurate.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from bottom-origin coordinates to top-origin coordinates. This makes field positions easier to compare with page layouts that count downward from the top.

**Data flow**: It receives a rectangle and the page height. It keeps the left and right positions, flips the vertical values around the page height, and returns a new rectangle in the adjusted coordinate system.

**Call relations**: All extraction paths use this when saving field locations. Radio option collection also uses it so every reported field position follows the same coordinate convention.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts form fields directly from page widget annotations. This is the fallback for PDFs that have visible fillable controls but no useful central form directory.

**Data flow**: It receives a PDF reader, scans each page’s annotations, keeps only widget annotations that have a field type and name, builds a field object, records its page number and flipped rectangle, enriches checkboxes when needed, and returns the list of discovered fields.

**Call relations**: `_extract_from_acroform` calls this when pypdf cannot find normal AcroForm fields. Inside, it relies on `_build_field_from_dict`, `_flip_rect`, and `_extract_checkbox_on_value` to turn raw PDF objects into useful metadata.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the main list of fillable fields from a PDF’s AcroForm, which is the standard PDF form structure. It also links those fields back to their page positions.

**Data flow**: It receives a PDF reader and asks pypdf for the raw fields. If there are none, it falls back to widget extraction. Otherwise it builds field objects, notes possible radio groups, scans page annotations to find where fields live, collects radio-button options, skips fields that cannot be located, sorts the final list in page-reading order, and returns it.

**Call relations**: The extract and fill commands both call this to understand the PDF before doing their work. It coordinates several helpers: name reconstruction, field building, rectangle flipping, radio option collection, and the widget fallback.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one radio-button choice to a radio group description. Radio buttons are grouped fields where one option among several can be selected.

**Data flow**: It receives one annotation, the group name, page information, page height, and the shared radio-group dictionary. It finds the annotation’s non-off appearance value, creates the group if needed, records the option’s value and flipped rectangle, and stores it in the group.

**Call relations**: `_extract_from_acroform` calls this while walking page annotations for fields that looked like radio groups. It hands coordinate conversion to `_flip_rect` so radio choices use the same position format as other fields.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a reading-order sort key for extracted fields. It helps the JSON output appear in a natural page, row, then left-to-right order.

**Data flow**: It receives a field. For radio groups it uses the first option’s rectangle; for other fields it uses the field rectangle. It groups nearby vertical positions into rough rows, then returns page number, row, and left position as the sort key.

**Call relations**: The AcroForm extraction flow uses this when ordering the combined field list. Its job is not to change fields, only to give sorting enough information to make the output easier for humans to read.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts an internal field object into plain JSON-friendly data. This is what makes the extracted field map easy to save, inspect, and edit.

**Data flow**: It receives a field object, starts with its name and kind, adds page and rectangle when known, then adds type-specific details such as checkbox on/off values, radio options, or choice options. It returns a normal dictionary ready for JSON encoding.

**Call relations**: The extract command calls this for every field returned by extraction. It is the bridge between Python dataclass objects and the JSON file that users or other tools consume.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a proposed value is allowed for a specific field. This prevents writing values that the PDF form does not understand.

**Data flow**: It receives a field and a value string. For checkboxes it compares the value with the known on and off values; for radio groups and choice fields it compares against their allowed option values. It returns an error message if the value is invalid, or nothing if it is acceptable.

**Call relations**: `_validate_fill_entries` calls this for entries that include a value. It provides the field-type-specific checks before `cmd_fill` writes anything to the output PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the `detect` command, which tells the user whether a PDF appears to contain fillable form fields. It is a quick yes-or-no check before trying extraction or filling.

**Data flow**: It receives command-line arguments. If the user did not provide exactly one PDF path, it prints usage and exits. Otherwise it opens the PDF, checks both normal form fields and orphaned widget fields, and prints a message saying whether fillable fields were found.

**Call relations**: The main command dispatcher calls this when the user chooses `detect`. It uses `_has_orphaned_widgets` as the second check after pypdf’s normal field lookup.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the `extract` command, which writes a JSON catalog of the PDF’s fillable fields. This gives users the field names, positions, and allowed values they need before filling the form.

**Data flow**: It receives command-line arguments for input PDF and output JSON. After validating the argument count, it opens the PDF, extracts field metadata, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written.

**Call relations**: The main dispatcher calls this for the `extract` subcommand. It depends on `_extract_from_acroform` for discovery and `_field_to_dict` for turning discovered fields into JSON-ready records.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the `fill` command, which writes values into a fillable PDF and saves a new PDF. It protects the user by validating field names, pages, and allowed values before writing.

**Data flow**: It receives command-line arguments for input PDF, values JSON, and output PDF. It loads the requested values, extracts actual PDF field metadata, checks the entries for errors, groups valid values by page, clones the PDF into a writer, updates each page’s form fields, writes the output file, and prints a summary.

**Call relations**: The main dispatcher calls this for the `fill` subcommand. It uses `_extract_from_acroform` to understand the PDF first, then `_validate_fill_entries` to stop bad input before handing values to pypdf’s PDF writer.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks a whole list of requested fill entries before the PDF is modified. It catches unknown field names, wrong page numbers, and invalid field values.

**Data flow**: It receives the user’s list of value entries and a lookup table of real fields by name. For each entry, it verifies that the field exists, that the page matches if a page was supplied, and that the value is allowed when present. It prints each error it finds and returns true if any error occurred.

**Call relations**: `cmd_fill` calls this before writing the output PDF. For value-specific rules it delegates to `_validate_fill_value`, while it performs the broader checks around field existence and page number itself.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Acts as the command-line entry point for the script. It chooses which subcommand to run based on the first argument.

**Data flow**: It reads the process command-line arguments. If there is no valid subcommand, it prints the allowed usage and exits with an error. Otherwise it passes the remaining arguments to the selected command function.

**Call relations**: This runs when the file is executed directly as `python formfill.py ...`. It is the front door that dispatches to `cmd_detect`, `cmd_extract`, or `cmd_fill` through the subcommand table.

*Call graph*: 1 external calls (exit).


### PDF Layout and Page Images
Command-line utilities inspect non-fillable PDF layouts, preview placement boxes, add text annotations, and render pages as PNG images.

### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line document processing`

Many PDFs look like forms but do not contain real fillable fields. This file helps bridge that gap. It first reads the visible page layout: words, long horizontal lines, and small square boxes that probably act like checkboxes. That extracted layout can be saved as JSON so another step can decide where answers belong.

It also supports a preview mode. Given a page image and a JSON file describing planned fields, it draws colored rectangles over the image: red for answer areas and blue for label areas. This is like putting transparent sticky notes on top of a scanned form to check that everything lines up before writing on the real document.

Finally, it can fill the PDF by adding FreeText annotations, which are pieces of visible text placed on top of the page. Before writing, it checks for common mistakes: boxes that are too short for the chosen font size, or fields that overlap each other. A key detail is coordinate conversion. Images usually measure from the top-left corner, while PDFs place annotations using a bottom-left style coordinate system. CoordMapper translates between those worlds so text lands in the intended spot.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: Converts a rectangle from the field description into the coordinate format needed for a PDF annotation. This matters because image coordinates and PDF coordinates count vertical position in opposite directions.

**Data flow**: It receives a bounding box as four numbers. If the source coordinates came from an image, it scales the box from image size to PDF page size, then flips the vertical position so it matches PDF annotation rules. If the coordinates are already in PDF-sized units, it only flips the vertical direction. It returns a four-number rectangle ready to give to the PDF writer.

**Call relations**: During PDF filling, _validate_and_fill creates a CoordMapper for each field’s page and asks this method to convert the requested content area before building the FreeText annotation.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: Reads one PDF page and records the visual clues that help identify form structure. It looks for text, long horizontal rules, and small square boxes that are likely checkboxes.

**Data flow**: It receives a page object from pdfplumber and the page number. It starts a PageLayout record with the page size, scans line objects for long horizontal lines, scans rectangle objects for checkbox-sized squares, and asks the page to extract its words. It returns a PageLayout containing the page’s visible form ingredients.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. The result is later enriched with row ranges and eventually converted into JSON for the extract command.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: Finds vertical bands between horizontal rules on a page. These bands can represent table rows or form rows.

**Data flow**: It receives a PageLayout that already contains horizontal rule positions. It sorts the rule positions from top to bottom, pairs each neighboring rule, and appends row range records with top, bottom, and height values. It changes the PageLayout in place and returns nothing.

**Call relations**: _extract_all_pages calls this right after _extract_page. It adds row information before the page layout is saved or turned into plain dictionaries.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: Scans an entire PDF and builds layout information for every page. It is the main worker behind the extract command.

**Data flow**: It receives a PDF file path. It opens the PDF with pdfplumber, loops through each page, extracts that page’s layout, computes row ranges for it, and collects all page layouts into a list. It returns the list of PageLayout objects.

**Call relations**: cmd_extract calls this after checking its command-line arguments. Inside, it hands each page to _extract_page and then to _compute_row_ranges so the final output has both raw page clues and derived row bands.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: Turns PageLayout objects into ordinary dictionaries that can be written as JSON. This makes the extracted layout easy for people and other tools to read.

**Data flow**: It receives a list of PageLayout records. For each page, it copies the page number, size, text elements, horizontal rules, checkbox candidates, and row ranges into a dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after _extract_all_pages has scanned the PDF. The command then serializes the returned dictionaries with JSON formatting and writes them to disk.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: Checks a field-description JSON file and writes answer text into a PDF as visible annotations. It stops before producing output if the requested fields look invalid or overlap.

**Data flow**: It receives an input PDF path, a fields JSON path, and an output PDF path. It reads the JSON, opens the PDF for copying, records each page’s size, and walks through each requested form field. For fields with text, it checks whether the box is tall enough, checks for overlaps with earlier boxes, converts the content rectangle into PDF annotation coordinates, creates a FreeText annotation, and adds it to the correct page. If errors were found, it prints them and exits. Otherwise it writes the new PDF and prints a placement summary.

**Call relations**: cmd_fill delegates almost all real work to this function. While running, it uses _rects_overlap to catch collisions between field boxes, uses CoordMapper to translate coordinates, and hands the final annotation objects to pypdf’s writer.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: Answers a simple geometry question: do two rectangles touch or cover the same space? It is used to prevent text fields from being placed on top of each other.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges. If one rectangle is completely to the side or above the other, it returns false; otherwise it returns true.

**Call relations**: _validate_and_fill calls this while checking each new field against fields already placed on the same page. A true result becomes a validation error.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the `extract` command. It reads a PDF and writes a JSON summary of the page layout.

**Data flow**: It receives the command arguments after the word `extract`. If the argument count is wrong, it prints usage help and exits. Otherwise it reads the input PDF path and output JSON path, scans all pages, converts the layouts into dictionaries, creates the output folder if needed, writes the JSON file, and prints counts of what it found.

**Call relations**: main selects this command when the user runs `layout.py extract`. It relies on _extract_all_pages for the PDF scan and _pages_to_dict for JSON-friendly output.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: Implements the `preview` command. It draws the planned field boxes onto an image so a person can visually check alignment before filling the PDF.

**Data flow**: It receives a page number, fields JSON path, input image path, and output image path. It reads the JSON, opens the image, draws red rectangles for content areas on the requested page, draws blue rectangles for label boxes when present, saves the marked-up image, and prints how many fields were highlighted.

**Call relations**: main selects this command when the user runs `layout.py preview`. It uses the field data directly and hands drawing work to Pillow, the image library.

*Call graph*: 5 external calls (Draw, open, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the `fill` command. It is the command-line wrapper that checks arguments and starts PDF writing.

**Data flow**: It receives the command arguments after the word `fill`. If there are not exactly three paths, it prints usage help and exits. Otherwise it passes the input PDF, fields JSON, and output PDF paths to _validate_and_fill. The filled PDF is produced by that helper.

**Call relations**: main selects this command when the user runs `layout.py fill`. This function does only the outer command check, then hands the real validation and annotation work to _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: Acts as the command-line entry point for the file. It chooses which operation to run based on the first word after the script name.

**Data flow**: It reads sys.argv, the list of command-line words used to start the script. If no valid subcommand is present, it prints a usage message and exits. Otherwise it looks up the matching command function and passes along the remaining arguments.

**Call relations**: When the file is run directly, the bottom `if __name__ == "__main__"` block calls main. main dispatches to one of the command functions: cmd_extract, cmd_preview, or cmd_fill.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command-line PDF rendering`

PDF files are not simple pictures; they are page documents that may contain text, shapes, and images. Some parts of a system may need plain image files instead, for example to show page thumbnails or feed pages into an image-reading tool. This file solves that conversion step.

When run, it takes two pieces of information: the path to an input PDF and the folder where the output images should go. It creates that folder if it does not already exist. Then it uses pdf2image, an outside library that renders PDF pages into image objects, at a fixed quality level of 200 dots per inch. After each page is rendered, the file checks whether the image is too large. If either width or height is over 1000 pixels, it shrinks the image while keeping the same shape, like resizing a photo without stretching it.

Each page is saved as a separate PNG named page_1.png, page_2.png, and so on. The script prints progress messages showing where each image was written and its final size. Without this file, a user or another tool would need some other way to turn PDF pages into predictable, reasonably sized image files.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts a PDF into one PNG image per page and writes those images into a chosen folder. It also keeps very large page images under a maximum size so the output is easier to store, view, and process.

**Data flow**: It receives a PDF file path and an output folder path. It creates the output folder if needed, asks pdf2image to render all PDF pages into image objects, optionally shrinks any image wider or taller than 1000 pixels, then saves each image as page_1.png, page_2.png, and so on. Its visible output is the set of PNG files on disk plus printed progress messages; it does not return a value.

**Call relations**: This is the worker function called by main after the command-line arguments have been checked. It relies on pathlib.Path to create and build folder paths, and on pdf2image.convert_from_path to do the actual PDF-to-image conversion before it saves the results.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Acts as the command-line doorway for the script. It checks that the user provided exactly two arguments: the input PDF and the output folder.

**Data flow**: It reads the command-line arguments from sys.argv. If the argument count is wrong, it prints a short usage message and exits with an error code. If the arguments are present, it passes them to render, which performs the conversion.

**Call relations**: This function runs when the file is executed directly as a script. Its job is to guard the entrance: either stop early with clear instructions when the command is incomplete, or hand the validated paths to render so the real work can begin.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
