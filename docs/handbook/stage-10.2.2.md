# Page content modeling and element discovery  `stage-10.2.2`

This stage is shared behind-the-scenes support for understanding a live browser page. Before an AI can click a button, read an article, or fill a form, the system must turn the messy visual page into a smaller, safer description it can reason about.

The main builder is page.py. It asks the browser for the accessibility tree, which is the browser’s structured list of useful controls such as links, buttons, text fields, and headings. It also records where elements are on the screen, including inside frames, then produces either an action-focused tree or a readable Markdown-style page view.

content.py wraps these abilities as browser content tools. It lets higher-level commands request a structured tree, plain text, or element search without needing to know browser details.

find.py is the search helper. It looks through the text form of the accessibility tree, checks whether an AI’s proposed match really exists, and formats matches clearly.

coordinate.py keeps sight and action aligned. It converts between real browser pixels and the vision model’s coordinate grid, so clicks land where the model intended.

## Files in this stage

### Content Tool Facade
High-level browser content entrypoints expose safe page reading and element-finding tools to callers.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file answers a simple question: “What is on the current browser page, and where is the thing I am looking for?” It wraps a browser session and exposes a few clear operations. One operation reads the page as an accessibility-style tree, which is a simplified outline of page elements. Another reads the page as markdown-like text, which is useful when the caller wants the page’s written content rather than its buttons and controls. A third searches the page tree for matches to a user’s query.

The file also protects callers from being flooded with too much data. Page trees and page text can be very large, so results are cut off at fixed limits and marked with a `truncated` flag when that happens. Think of it like asking a librarian for a preview rather than receiving the entire archive at once.

`BrowserContent` does not know how to control the browser directly. Instead, it depends on a `BrowserContentSession`, which can provide a tab, a page reader, and tab details. This keeps the content logic separate from the browser plumbing. The small `_tab_id` helper turns a tab identifier from incoming JSON data into an integer when possible, so callers can request a specific tab without caring too much about whether the value arrived as a number or string.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This describes the method that a page reader must provide to return a structured view of a browser tab. The structured view is used when the system needs to understand page elements, such as links, buttons, or a specific referenced element.

**Data flow**: It receives a browser tab, a filter choice such as all elements or only interactive ones, and optionally a reference to one element. It should read the page through that lens and return text for the matching tree, or return nothing if the referenced element cannot be found.

**Call relations**: This is part of the expected interface used by `BrowserContent.tree`. A concrete browser reader elsewhere supplies the real behavior, while this file only states what `BrowserContent` needs from it.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This describes the method that a page reader must provide to turn a browser tab into readable page text. It is used when the caller wants the page’s written content rather than a tree of page controls.

**Data flow**: It receives a browser tab, reads the visible or available document content, and returns a markdown-style text string.

**Call relations**: This method is called by `BrowserContent.get_page_text`. The actual browser-specific implementation lives outside this file; this file depends on the promise that such a method exists.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This describes how `BrowserContent` asks for the browser tab it should work on. The caller may name a tab, or leave it blank to use the session’s current or default tab.

**Data flow**: It receives an optional tab id. It returns the matching `PageTab`, which represents the browser page that later content-reading steps will inspect.

**Call relations**: This is used by `BrowserContent.tree` and `BrowserContent.get_page_text` before they can read anything from the page. The session implementation decides how to find or create the right tab.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This describes how `BrowserContent` gets the object that knows how to extract content from a page. It keeps the browser-reading details outside this file.

**Data flow**: It takes no content arguments. It returns a page reader object that can produce either a tree view or markdown text for a tab.

**Call relations**: This is called after a tab is selected, especially by `BrowserContent.tree` and indirectly by `BrowserContent.get_page_text` through the reader it returns.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This describes how `BrowserContent` asks for extra facts about a tab, such as metadata that should travel with page text. It lets text results include context about where the text came from.

**Data flow**: It receives a tab object and returns a JSON-style dictionary with information about that tab.

**Call relations**: This is used by `BrowserContent.get_page_text` after the page text has been read, so the final response can include both the text and tab details.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This reads a browser tab as a structured page tree. Callers use it when they need a compact outline of page elements, optionally narrowed to one referenced element or one kind of element.

**Data flow**: It receives request arguments, looks for a `tab_id`, turns that into an integer with `_tab_id`, and asks the browser session for that tab. It also reads an optional `ref_id`, asks the page reader for the tree using the requested filter, and returns the tree text. If the reader says the referenced element does not exist, it returns a clear message saying no element was found.

**Call relations**: `BrowserContent.read_page` calls this when it needs a tree response for a user-facing read operation. `BrowserContent.find` calls it before searching the page. It hands off tab selection to the browser session and content extraction to the page reader.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the page-tree reading command that returns a JSON-style response. It lets callers choose a safe filter, then limits the returned tree so very large pages do not overwhelm the system.

**Data flow**: It receives request arguments and reads the optional `filter` value. Only known filter values are accepted; anything else falls back to `all`. It asks `BrowserContent.tree` for the page tree, cuts the result down to the maximum allowed size, and returns both the shortened tree and a flag saying whether anything was cut off.

**Call relations**: This is a higher-level wrapper around `BrowserContent.tree`. It is the friendlier request-facing shape: validate the filter, fetch the tree, and package the answer with truncation information.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This reads a browser tab as plain page text rather than as a tree of elements. It is useful for summarizing or quoting the readable content of a page.

**Data flow**: It receives request arguments, extracts an optional `tab_id` using `_tab_id`, and asks the session for that tab. It then asks the page reader for markdown-style text, cuts the text to the maximum allowed size, marks whether it was truncated, and adds tab information from the browser session to the returned dictionary.

**Call relations**: This runs alongside the tree-reading path but uses the page reader’s `markdown` capability instead. It relies on the browser session for both the tab itself and the extra tab information included in the final response.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page tree for elements that match a user’s query. It can use a simple built-in parser, or, when supplied, a `FindCompleter` such as a language-model helper to interpret the page tree more flexibly.

**Data flow**: It receives request arguments and reads the required `query` as a string. It fetches the full page tree through `BrowserContent.tree`. If no completer is provided, it searches the tree directly with `parse_tree_matches`. If a completer is provided, it sends the query and a size-limited copy of the page tree to that completer, then turns the completer’s reply into matches with `resolve_find_reply`. It returns the match list and a human-readable summary made by `format_matches`.

**Call relations**: This builds on `BrowserContent.tree` because searching starts from the same structured view of the page. It then delegates the actual matching either to local parsing helpers or to the optional completer, and finishes by formatting the results for the caller.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This converts a tab id from incoming JSON-style data into an integer, or returns nothing if no usable tab id was provided. It makes callers tolerant of tab ids arriving as numbers or as strings.

**Data flow**: It receives a value that may be an integer, a floating-point number, a non-empty string, or something else. Integers are returned as-is, floats and strings are converted to integers, and missing or unsupported values become `None`.

**Call relations**: `BrowserContent.tree` and `BrowserContent.get_page_text` use this before asking the browser session for a tab. That means the rest of the content-reading code can work with a clean optional integer instead of many possible input shapes.

*Call graph*: called by 2 (get_page_text, tree).


### Coordinate Mapping
Pixel-to-vision-grid translation keeps model-observed locations aligned with real browser actions.

### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `cross-cutting`

A browser viewport may be large, but an AI vision model may not receive the screenshot at that exact size. Some models shrink large images before reasoning about them, and Gemini uses a fixed 0-to-1000 coordinate grid no matter how big the image is. This file is the small measuring tool that keeps those worlds in sync.

It defines two simple frozen data shapes: Size for width and height, and Coord for x and y positions. The main idea is: before asking a model about a screenshot, the system needs to know the coordinate space the model is using. Then, when the model says “click here,” that point must be converted back into real browser pixels.

For Claude-family models, the file estimates the largest screenshot dimensions that will not be further shrunk by the service. For Gemini, it can report a fixed 1000 by 1000 model grid. The conversion functions then scale points proportionally between the model’s view and the browser’s viewport. Like reading a map, the location is the same place, but the ruler has a different scale. Without this file, clicks could land in the wrong spot because the model and the browser would be talking in different coordinate systems.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: Figures out how large a screenshot can be before Claude’s vision system would shrink it on the server side. This lets the project work with the same image size the model will actually see.

**Data flow**: It starts with the browser viewport size. It first scales the longer side down if it is over Claude’s maximum long edge, then checks whether the total pixel count is still too high and shrinks both sides again if needed. It returns a new Size containing the final width and height.

**Call relations**: This is the fallback sizing rule used by effective_model_size. When no explicit model coordinate size is supplied, effective_model_size calls this function so later coordinate conversions are based on the screenshot size Claude will reason over.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: Decides which coordinate space the model is using. It uses a supplied override when one exists, otherwise it computes the screenshot size from the viewport.

**Data flow**: It receives the browser viewport size and, optionally, a model-specific size. If the optional size is present, it returns that directly. If not, it passes the viewport to compute_screenshot_dimensions and returns the computed Size.

**Call relations**: This is the shared helper used by both model_to_viewport and viewport_to_model. Those conversion functions call it first so they know which scale to use before translating a point.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point reported by the AI model into real browser viewport pixels. This is needed before dispatching an input action such as a click or mouse move.

**Data flow**: It receives a model-space coordinate, the browser viewport size, and optionally a model coordinate size. It asks effective_model_size for the model’s width and height, then scales x and y from model units into viewport pixels. It returns a new Coord in browser coordinates.

**Call relations**: This function sits at the point where model output becomes browser action. It relies on effective_model_size to pick the correct ruler, then creates the browser-space Coord that other browser input code can use.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: Returns the special coordinate grid used by a named model, when the model has one. In particular, it recognizes Gemini models as using a fixed 1000 by 1000 grid.

**Data flow**: It receives a model name, or nothing. If the name exists and contains “gemini” regardless of letter case, it returns a Size of 1000 by 1000. For other model names, it returns None, meaning the normal screenshot-size coordinate space should be used.

**Call relations**: This function is a model-specific lookup. Its result can be passed as the optional model_size to the conversion functions, telling them to use Gemini’s fixed grid instead of calculating a Claude-style screenshot size.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a real browser pixel position back into the coordinate space used by the model. This is useful when browser positions need to be described in the same scale as the screenshot or model grid.

**Data flow**: It receives a browser viewport coordinate, the viewport size, and optionally a model coordinate size. It asks effective_model_size for the model’s width and height, then scales x and y from viewport pixels into model units. It returns a new Coord in model coordinates.

**Call relations**: This is the reverse path of model_to_viewport. It uses the same effective_model_size helper so both directions agree about the model’s coordinate space.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### Element Discovery
Accessibility-tree search utilities locate, validate, and format page elements for callers and humans.

### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

This file is the “consumer” for a simple text format that represents a browser accessibility tree. An accessibility tree is like a page map: each line describes something on the page, such as a button or textbox, with its role, visible name, internal reference, and sometimes screen coordinates. Without this file, other code could receive a tree but would not have a safe, consistent way to pull out usable element matches from it.

The file does three main jobs. First, it reads tree lines and turns them into small records containing the element reference, role, name, coordinates, and searchable lowercase text. Second, it offers a simple fallback search: split the user’s query into words and return tree entries whose line contains all those words. Third, it can take a reply from a language model and “ground” it against the real tree. That means it only accepts references that actually appear in the tree, and it copies the trusted role, name, and coordinates from the tree instead of trusting the reply. This is important because an AI might invent or misremember a reference.

A fixed maximum keeps results short. The formatting helper then turns structured matches back into readable lines, adding a note if more matches may exist.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function reads the text accessibility tree and extracts the useful parts of each element line. It gives later search steps a clean list of element records instead of forcing them to parse raw text repeatedly.

**Data flow**: It takes the full tree text as input. It checks each line for an element role and reference, optionally reads screen coordinates, and builds a record with the reference, role, name, coordinates, and a lowercase copy of the line for searching. Lines that do not look like valid element lines are skipped, and the result is a list of parsed entries.

**Call relations**: This is the shared first step for both local searching and AI-reply validation. parse_tree_matches calls it before looking for query words, and resolve_find_reply calls it before checking whether a suggested reference is real.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This small helper turns a parsed tree entry into the standard match shape used by the rest of this file. It keeps result records consistent whether the match came from direct search or from a checked AI reply.

**Data flow**: It receives one parsed tree entry and a reason string. It copies the entry’s reference, role, name, and coordinates, adds the reason, and returns a new match record. It does not change the original entry.

**Call relations**: parse_tree_matches uses it when a tree entry matches the query words. resolve_find_reply uses it after confirming that a reply’s reference exists in the tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple built-in search over the accessibility tree. It is useful when the system wants quick matches based only on the query words, without relying on an outside model.

**Data flow**: It takes the tree text and a user query. It lowercases the query, pulls out simple word-like terms longer than one character, parses the tree into entries, and keeps entries whose lowercase line contains every query term. It returns up to the configured maximum number of match records, with an empty reason because the match came from plain word matching.

**Call relations**: It starts by asking tree_entries to turn raw tree text into records. For each matching record, it asks _match_payload to produce the standard result format. It is one path for producing matches that can later be shown with format_matches.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function checks and cleans a find answer, especially one produced by a language model. It prevents made-up or stale element references from being used by accepting only references that are present in the current tree.

**Data flow**: It takes a reply text and the original tree text. It parses the tree into a lookup table by reference, reads the reply line by line, notices special lines like NO_MATCHES and MORE, extracts references, rejects missing or duplicate references, and builds trusted match records using data from the tree. It returns two things: the accepted matches and a true-or-false flag saying whether the reply claimed more matches exist.

**Call relations**: This function uses tree_entries to build the trusted source of element data. When a reply line names a valid reference, it uses _match_payload to create the final match. It is the safety gate between a free-form find reply and later code that might act on or display those matches.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns structured match records into readable text. It is used when the system needs to show found elements in a compact, human-friendly form.

**Data flow**: It takes a list of match records and an optional flag saying whether more matches exist. For each match, it creates a line with the reference, role, name, and coordinates, and adds the reason if one is present. If the more flag is true, it appends a final hint asking the user to refine the query. It returns one joined text block.

**Call relations**: This is the final presentation step after matches have already been found or validated. It does not call other helpers in this file; it simply converts the match data produced by parse_tree_matches or resolve_find_reply into display text.


### Page Representation Rendering
Live browser pages are captured and rendered into AI-usable action trees and reading-style Markdown views.

### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A browser page is visually rich, but an AI agent needs a structured map: what buttons, links, fields, headings, and images exist, where they are, and how to refer to them later. This file builds that map. It talks to Chrome through CDP, the Chrome DevTools Protocol, which is a browser control interface. It collects two views of the page: the DOM snapshot, which gives geometry and raw element details, and the accessibility tree, which gives human-facing roles and names such as “button” or “search box.” It then joins those views together using browser node IDs.

Frames are a major concern. A page can contain iframes, which are like smaller pages embedded inside the main page. Some iframes live in the same browser target, while out-of-process iframes need a separate CDP session. This file stitches them into one tree so the caller sees one coherent page.

The rendered page output includes stable-looking refs such as `e12` or `f1e3`, plus center coordinates scaled to the model’s coordinate space. Those refs are later resolved back to real browser nodes for clicking or typing. The Markdown renderer is a quieter “reading mode” that keeps headings, links, lists, and paragraphs instead of exposing every actionable detail.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference, such as `e12` or `f1e3`, into the frame prefix and the browser backend node number. It is used to check whether a model-provided reference has the expected shape.

**Data flow**: It receives a reference string. If the string matches the allowed pattern, it returns the prefix part and the numeric backend node ID; if not, it returns nothing.

**Call relations**: When rendering starts from a specific reference, `_PageRenderer.render` uses this to find the target node. When an action needs to use a reference, `BrowserPage.resolve_ref` uses it to reject invented or malformed refs.

*Call graph*: called by 2 (resolve_ref, render).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Describes the browser-control call used to send a command to Chrome and receive a JSON reply. This is a protocol method, meaning it defines what a compatible CDP connection must provide.

**Data flow**: It receives a command name, optional parameters, and an optional session ID. A real implementation sends that to the browser and returns the browser’s JSON response.

**Call relations**: This file’s snapshot code calls it in `fetch_target` to enable browser domains, capture snapshots, query device scale, and fetch accessibility trees. Another part of the system, `Settle._flush_page_tasks`, also relies on the same interface.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a short, stable sequence number for a frame so refs can be prefixed consistently, such as `f1`. It lets embedded frames have distinct element references.

**Data flow**: It receives a browser frame ID and returns a number assigned to that frame. That number becomes part of the text reference shown to the model.

**Call relations**: `BrowserPage._snapshot_oop` calls this when it discovers an out-of-process iframe and needs to name that frame in the unified page tree.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Describes how a browser session exposes its CDP connection. Code in this file uses that connection to ask Chrome for page structure and element geometry.

**Data flow**: It takes the session object and returns an object that can send CDP commands. Nothing is changed by the interface itself.

**Call relations**: The `BrowserPage` methods depend on this session-level hook whenever they need to capture a page, inspect an iframe, attach to another target, or locate a referenced element.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Describes the setup step for a newly attached browser session. It gives the wider browser layer a chance to prepare that session before this file uses it.

**Data flow**: It receives a new CDP session ID. A real implementation initializes browser state for that session and returns when it is ready.

**Call relations**: `BrowserPage._oop_session` calls this after attaching to an out-of-process iframe, before caching and using that iframe’s session.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely converts a JSON value into a floating-point number. It avoids crashes when browser data is missing or not numeric.

**Data flow**: It receives a value and a fallback number. Numeric values become floats; anything else becomes the fallback.

**Call relations**: `_parse_document` uses it for scroll positions and bounds from DOM snapshots. `fetch_target` uses it to read the browser’s device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Looks up one HTML attribute from Chrome’s compact snapshot format. Chrome stores attribute names and values as indexes into a shared string table, so this function translates that compact form back into text.

**Data flow**: It receives the shared string list, an attribute list, and the desired attribute name. It scans name/value pairs and returns the matching value text, or nothing if absent.

**Call relations**: `_parse_document` calls it while reading inputs, images, and iframes so it can remember input types, image sources, and iframe sources.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Reads one document from Chrome’s DOM snapshot and extracts the geometry and special element details needed later. It is the bridge from Chrome’s raw compressed data to this file’s simpler internal shape.

**Data flow**: It receives one raw document snapshot, the shared string table, and the device pixel ratio. It validates and decodes node IDs, element bounds, scroll offsets, pointer cursor style, input types, image sources, and iframe links, then returns a `_RawDoc` record.

**Call relations**: `parse_snapshot` calls this once for each document in the snapshot. It relies on `_float` for numeric cleanup and `_attr` for element attributes.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome’s full DOM snapshot into per-frame document data with page coordinates that all line up. It solves the problem that each iframe reports positions in its own local space.

**Data flow**: It receives the raw snapshot, device pixel ratio, and a starting origin. It parses each document, walks parent-to-child iframe links to accumulate origins, adjusts element bounds into shared page coordinates, and returns `DocData` records.

**Call relations**: `fetch_target` calls this after Chrome returns a DOM snapshot. It delegates each raw document to `_parse_document` and then prepares the frame information that later gets joined with accessibility trees.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Captures everything needed from one browser target: DOM geometry, accessibility nodes, and same-target frame relationships. This is the main CDP collection step.

**Data flow**: It receives a CDP connection, session ID, frame-ref naming helpers, and an origin. It enables needed browser domains, captures a DOM snapshot, reads device scale, parses geometry, fetches accessibility trees for each document, and returns a `FrameSnapshot` tree.

**Call relations**: `BrowserPage._snapshot_target` calls this for the main page and for attached iframe targets. Inside, it uses `Cdp.send` for browser requests and `parse_snapshot` to make Chrome’s DOM data usable.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain value from an accessibility-tree field. Chrome wraps many accessibility values in small objects, and this removes that wrapper.

**Data flow**: It receives a JSON value that may be a dictionary. If it contains a `value`, that value is turned into text; otherwise it returns an empty string.

**Call relations**: Both `_PageRenderer._render_node` and `_MarkdownRenderer._walk` use it to read roles and names before deciding what to show.

*Call graph*: called by 2 (_walk, _render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Finds a named property on an accessibility node, such as `checked`, `disabled`, `url`, or heading `level`. These properties describe state beyond the basic role and name.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s properties and returns the property’s underlying value, or nothing if the property is absent.

**Call relations**: The page renderer uses it to filter hidden nodes and print state details. The Markdown renderer uses it for links and heading levels. `_format_extras` uses it to build readable suffixes.

*Call graph*: called by 4 (_render_content, _walk, _render_node, _format_extras); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node can be skipped without losing useful information. Some nodes are just containers, like empty boxes in a packing crate.

**Data flow**: It receives an accessibility node, its role, and its name. It returns true only for skip-worthy roles that have no name and no important state properties.

**Call relations**: `_PageRenderer._render_node` uses this to keep the rendered tree shorter while still descending into the skipped node’s children.

*Call graph*: called by 1 (_render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so rendered page output stays readable and compact. It adds an ellipsis when text is cut.

**Data flow**: It receives text and a maximum length. Text within the limit is returned unchanged; longer text is shortened to fit.

**Call relations**: `_PageRenderer._render_node`, `_format_extras`, and `_image_name` call it before putting names, values, and filenames into model-facing output.

*Call graph*: called by 3 (_render_node, _format_extras, _image_name).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Finds a useful fallback name for an image from its source URL. This helps when an image has no accessible label.

**Data flow**: It receives an optional image source URL. It extracts the final filename from the URL path, truncates it if needed, and returns it only if it looks like a file name.

**Call relations**: The page renderer uses it for unnamed image nodes. The Markdown renderer uses it when creating image-like Markdown entries.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (_render_content, _render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the extra state text printed after a rendered page node, such as input type, current value, checked state, or URL. This gives the model important context without making every property a separate line.

**Data flow**: It receives an accessibility node and optional geometry/details for the DOM node. It reads known properties, filters out unsafe or noisy URL forms, truncates long values, and returns a formatted suffix string.

**Call relations**: `_PageRenderer._render_node` calls this when it is about to add one line to the page tree.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (_render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that matches a ref prefix. For example, it can locate the frame represented by `f1` inside the nested frame tree.

**Data flow**: It receives the root frame snapshot and a prefix string. It searches the root and child frames recursively, returning the matching frame or nothing.

**Call relations**: `_PageRenderer.render` uses this when rendering only the subtree for a specific ref.

*Call graph*: called by 1 (render).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node that corresponds to a browser backend DOM node ID. This joins a ref’s numeric ID back to the accessibility tree.

**Data flow**: It receives a frame snapshot and backend node ID. It scans accessibility nodes and returns the matching accessibility node ID, or nothing.

**Call relations**: `_PageRenderer.render` uses this after `split_ref` and `_frame_by_prefix` identify where a requested ref should start.

*Call graph*: called by 1 (render).


##### `_PageRenderer.render`  (lines 489–504)

```
def render(self, root: FrameSnapshot, ref: str | None) -> str | None
```

**Purpose**: Starts rendering the accessibility tree into the action-focused page format. It can render the whole page or only the subtree under a specific ref.

**Data flow**: It receives the root frame snapshot and an optional ref. If a ref is given, it parses the ref, finds the frame and node, and renders from there; otherwise it starts at the root accessibility node. It returns the collected lines as one string, or nothing if the ref cannot be resolved.

**Call relations**: `render_page` creates the renderer and calls this. This method hands actual tree traversal to `_PageRenderer._render_node`.

*Call graph*: calls 4 internal fn (_render_node, _frame_by_prefix, _node_by_backend, split_ref).


##### `_PageRenderer._coord_str`  (lines 506–510)

```
def _coord_str(self, geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for the rendered page output. The point is scaled into the model’s coordinate system.

**Data flow**: It receives optional geometry. If bounds exist, it calculates the center of the rectangle, applies the x and y scale factors, and returns text like `(x=10,y=20)`; otherwise it returns an empty string.

**Call relations**: `_PageRenderer._render_node` calls this while composing each visible line.

*Call graph*: called by 1 (_render_node).


##### `_PageRenderer._splice`  (lines 512–515)

```
def _splice(self, frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts an iframe’s child document into the rendered tree at the iframe node. This makes embedded pages appear in the right place, like pages tucked into a folder.

**Data flow**: It receives the current frame, an optional backend node ID, and the depth to render at. If that backend node has a child frame with a root node, it renders that child frame there.

**Call relations**: `_PageRenderer._descend` calls this after rendering normal accessibility children, so frame contents are stitched into the same text tree.

*Call graph*: calls 1 internal fn (_render_node); called by 1 (_descend).


##### `_PageRenderer._render_node`  (lines 517–567)

```
def _render_node(self, frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Renders one accessibility node and, when appropriate, its descendants. This is the core of the action-focused page view.

**Data flow**: It receives a frame, accessibility node ID, depth, and parent name. It skips repeated, hidden, unhelpful, offscreen, or filtered-out nodes; otherwise it builds a line with role, name, ref, coordinates, and state, then continues into children.

**Call relations**: It is called first by `_PageRenderer.render`, then recursively through `_PageRenderer._descend` and `_PageRenderer._splice`. It uses helper functions to read accessibility values, decide skips, format extras, shorten text, and calculate coordinates.

*Call graph*: calls 8 internal fn (_coord_str, _descend, _ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); called by 3 (_descend, _splice, render); 2 external calls (as_list, as_str).


##### `_PageRenderer._descend`  (lines 569–579)

```
def _descend(self, frame: FrameSnapshot, backend_id: int | None, child_ids: list[str], depth: int, parent_name: str) -> None
```

**Purpose**: Walks from a node into its child nodes and any child frame attached at the same DOM element. It keeps the traversal order simple and predictable.

**Data flow**: It receives the current frame, optional backend node ID, child accessibility IDs, render depth, and parent name. It renders each child, then splices in an iframe child if present.

**Call relations**: `_PageRenderer._render_node` calls this whenever it should continue below the current node. This method loops back to `_render_node` and uses `_splice` for frame contents.

*Call graph*: calls 2 internal fn (_render_node, _splice); called by 1 (_render_node).


##### `render_page`  (lines 582–599)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Public helper that turns a `FrameSnapshot` into the compact page-tree text used by the model. It applies viewport and model-size scaling before rendering.

**Data flow**: It receives the root snapshot, viewport size, optional model size, filter choice, depth limit, and optional ref. It computes the effective target size, creates a `_PageRenderer`, and returns the rendered text.

**Call relations**: `BrowserPage.tree` calls this after taking a fresh snapshot. It delegates the detailed traversal to `_PageRenderer.render`.

*Call graph*: called by 1 (tree); 2 external calls (__init__, effective_model_size).


##### `_MarkdownRenderer.render`  (lines 613–617)

```
def render(self, root: FrameSnapshot) -> str
```

**Purpose**: Starts rendering a frame snapshot into a Markdown reading view. This view is meant for understanding page content rather than choosing click targets.

**Data flow**: It receives the root frame. If a root accessibility node exists, it walks the tree, flushes any remaining inline text into blocks, and returns the blocks separated by blank lines.

**Call relations**: `render_markdown` creates this renderer and calls it. The work is passed to `_MarkdownRenderer._walk` and `_MarkdownRenderer._flush`.

*Call graph*: calls 2 internal fn (_flush, _walk).


##### `_MarkdownRenderer._emit`  (lines 619–622)

```
def _emit(self, text: str) -> None
```

**Purpose**: Adds one finished Markdown block, avoiding empty text and immediate duplicates. It keeps the reading output tidy.

**Data flow**: It receives text, trims surrounding whitespace, and appends it to the block list if it is meaningful and not the same as the last block.

**Call relations**: `_MarkdownRenderer._flush` and `_MarkdownRenderer._render_content` call this when inline text or structured content becomes a complete block.

*Call graph*: called by 2 (_flush, _render_content).


##### `_MarkdownRenderer._flush`  (lines 624–627)

```
def _flush(self) -> None
```

**Purpose**: Turns accumulated inline words into a paragraph block. It is used at natural boundaries such as paragraphs, headings, and frame changes.

**Data flow**: It checks the inline text buffer. If there is text, it joins the pieces with spaces, emits the paragraph, and clears the buffer.

**Call relations**: The renderer calls this at the end of rendering, during tree walking, and before adding block-style content in `_render_content`.

*Call graph*: calls 1 internal fn (_emit); called by 3 (_render_content, _walk, render).


##### `_MarkdownRenderer._walk`  (lines 629–649)

```
def _walk(self, frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and decides where Markdown content should be gathered. It also crosses into child frames at the right point.

**Data flow**: It receives a frame, accessibility node ID, and parent name. It avoids cycles and hidden nodes, reads the node’s role and name, renders meaningful content, walks children, flushes around block nodes, and then walks any child frame.

**Call relations**: `_MarkdownRenderer.render` starts this walk. It uses `_render_content` for role-specific Markdown choices and `_flush` to separate blocks.

*Call graph*: calls 4 internal fn (_flush, _render_content, _ax_property, _ax_value); called by 1 (render); 2 external calls (as_list, as_str).


##### `_MarkdownRenderer._render_content`  (lines 651–681)

```
def _render_content(self, frame: FrameSnapshot, backend_id: int | None, role: str, name: str, node: JsonDict) -> None
```

**Purpose**: Converts one accessibility node into Markdown-like content when it represents readable page material. It preserves structure such as headings, links, list items, and images.

**Data flow**: It receives the frame, optional backend ID, role, name, and node. Depending on the role, it emits a block, appends inline text, formats a heading, creates a link, creates a bullet, or adds an image label.

**Call relations**: `_MarkdownRenderer._walk` calls this for each non-duplicate node. It uses `_ax_property` for details like heading level and link URL, and `_image_name` for image fallbacks.

*Call graph*: calls 4 internal fn (_emit, _flush, _ax_property, _image_name); called by 1 (_walk).


##### `render_markdown`  (lines 684–689)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Public helper that turns a `FrameSnapshot` into a Markdown reading view. It is useful when the caller wants the page’s written structure instead of action refs.

**Data flow**: It receives the root snapshot, creates a Markdown renderer, and returns the rendered Markdown string.

**Call relations**: `BrowserPage.markdown` calls this after taking a fresh snapshot.

*Call graph*: called by 1 (markdown); 1 external calls (__init__).


##### `BrowserPage.tree`  (lines 698–706)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Captures the current page and returns the model-facing page tree. Callers use this to let the model inspect clickable and readable elements.

**Data flow**: It receives a tab, a filter type, and an optional ref. It takes a fresh snapshot, renders it with viewport and model-size scaling, and returns the resulting text or nothing if the ref cannot be rendered.

**Call relations**: This is a high-level entry into the file’s page-tree flow. It calls `BrowserPage.snapshot` first, then hands the snapshot to `render_page`.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 708–709)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Captures the current page and returns a Markdown reading view. Callers use this when they need page content in a cleaner document-like form.

**Data flow**: It receives a tab, snapshots the page, renders that snapshot as Markdown, and returns the text.

**Call relations**: This is the high-level reading-mode path. It calls `BrowserPage.snapshot` and then `render_markdown`.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 711–724)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a model-facing ref back into the frame and backend node ID needed for browser actions. It also catches invented or stale refs and reports them as model mistakes.

**Data flow**: It receives a tab and ref string. It parses the ref, looks up the frame prefix in the tab’s registered frames, and returns the frame node plus backend ID; invalid or unknown refs raise `HallucinationError`.

**Call relations**: `BrowserPage.ref_point` calls this before asking Chrome where an element is. It relies on refs registered by `BrowserPage.snapshot`.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 726–752)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the screen point to use for an element ref, usually for clicking. It scrolls the element into view first so Chrome can report a useful box.

**Data flow**: It receives a tab and ref. It resolves the ref, tells Chrome to scroll the element into view, asks for its content quadrilateral or box model, averages the four corners, adds the frame origin, and returns integer x/y coordinates. If Chrome cannot find the node, it raises `HallucinationError`.

**Call relations**: This is the action-support path after the model chooses a ref from `tree` or similar output. It uses `BrowserPage.resolve_ref` and `_coord_float_or_default` while talking to CDP.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 754–758)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Builds a fresh unified snapshot of the tab and records which frame prefix maps to which browser session. This keeps later refs resolvable.

**Data flow**: It receives a tab. It snapshots the main target, clears old ref-frame mappings, registers every frame in the new snapshot, and returns the root frame snapshot.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates capture to `_snapshot_target` and bookkeeping to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 760–777)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Captures one browser target and, if allowed, attaches any out-of-process iframe snapshots beneath it. It is the recursive building block for full-page snapshots.

**Data flow**: It receives the tab, CDP session ID, frame prefix, origin, and current frame depth. It fetches the target snapshot and, while under the depth limit, attaches extra iframe targets before returning the root frame.

**Call relations**: `BrowserPage.snapshot` calls this for the main page. `_snapshot_oop` calls it again for out-of-process iframes. It uses `fetch_target` for the actual CDP capture and `_attach_oop_frames` for iframe expansion.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 779–787)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Searches a captured frame tree for out-of-process iframes and tries to attach their snapshots. These iframes need special treatment because Chrome exposes them through separate targets.

**Data flow**: It receives the tab, root frame, and current depth. It walks the frame tree, tries to snapshot each listed out-of-process iframe, and inserts successful child snapshots into the parent frame’s children.

**Call relations**: `BrowserPage._snapshot_target` calls this after a target is captured. It calls `_snapshot_oop` for each iframe that needs a separate session.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 789–815)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Captures one out-of-process iframe, if Chrome can describe and attach to it. It turns an iframe placeholder into a real child frame snapshot.

**Data flow**: It receives the tab, parent frame, iframe backend node ID, and depth. It asks Chrome for the iframe’s frame ID, obtains or creates a CDP session, calculates the child origin from iframe bounds, and recursively snapshots that target. Failures are logged and return nothing.

**Call relations**: `BrowserPage._attach_oop_frames` calls this while walking the frame tree. It uses `_oop_session` to get access, `PageTab.frame_seq` to name the frame prefix, and `_snapshot_target` to capture the child.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 817–820)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame prefix in the tab so later refs can be resolved. Without this, a ref like `f1e3` could be printed but not used.

**Data flow**: It receives a tab and a frame snapshot. It stores a `FrameNode` for the frame’s prefix, then repeats the same process for child frames.

**Call relations**: `BrowserPage.snapshot` calls this after building the frame tree. `BrowserPage.resolve_ref` later depends on the mapping it creates.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 822–835)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing a cached one when possible. This avoids repeatedly attaching to the same iframe target.

**Data flow**: It receives a browser frame ID. If a session is cached, it returns it; otherwise it asks Chrome to attach to that target, initializes the new session, stores it, and returns the session ID. If attaching fails, it returns nothing.

**Call relations**: `BrowserPage._snapshot_oop` calls this before it can snapshot an out-of-process iframe.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 838–847)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts coordinate values from Chrome into floats, accepting numbers and numeric strings. It provides a fallback only for missing values.

**Data flow**: It receives a coordinate-like value and a default. Numbers become floats, non-empty strings are parsed as floats, `None` becomes the default, and other values raise a validation error.

**Call relations**: `BrowserPage.ref_point` uses this while averaging the four corners of an element’s box returned by Chrome.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).
