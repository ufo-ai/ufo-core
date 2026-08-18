# Page reading, element lookup, and coordinate mapping  `stage-10.3.4`

This stage is behind-the-scenes support for the main browser work loop. Its job is to turn a live web page into something the AI can understand, then turn the AI’s choices back into real places on the screen.

The page reader is the main bridge. It takes the current browser page and produces readable text, including a simplified “accessibility tree,” which is a screen-reader-style outline of buttons, links, fields, and other page parts. The content service wraps this lower-level page reading so higher-level commands can ask simple questions like “what is on the page?” or “where is this element?”

The find logic searches that text outline and converts possible hits into structured, safer matches the rest of the system can use. This helps avoid treating raw text as if it were a real page object.

The coordinate mapper completes the loop. Screenshots may be resized before a vision model sees them, so it translates model coordinates back to the browser’s real pixel positions, keeping clicks and typing aimed correctly.

## Files in this stage

### Content access service
High-level browser-content operations expose page reading, text extraction, and element search to the rest of the system.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file solves a simple but important problem: when an automation agent looks at a web page, it needs a safe, consistent way to ask, “What is on this page?” and “Where is the thing I am looking for?” Without this layer, every caller would need to know how to pick a browser tab, ask for the page structure, trim huge results, and interpret search results.

The file defines two protocol classes, which are like contracts: they say what a browser session and a page reader must be able to do, without saying how they do it. A page reader can return either a structured page “tree” or the page as markdown text. A browser session can choose a tab, provide a page reader, and report tab details.

The main class, BrowserContent, uses those contracts to offer practical actions. It can read a page tree, return a shortened version for display, get the full page text as markdown, or search the page for a query. The search can work in two ways: a simple built-in text match, or an optional AI-style completer that receives a prompt and the page tree and returns likely matches.

The file also protects callers from overly large page dumps by cutting long outputs to fixed limits and marking them as truncated. This is like giving someone a readable excerpt instead of dropping a whole encyclopedia on their desk.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This defines the promise that a page reader can return a structured view of a browser page. That structure is used when the system needs to inspect elements, not just plain text.

**Data flow**: It receives a browser tab, a filter saying what kind of elements to include, and an optional reference to a specific element. It is expected to turn that page information into a text tree, or return nothing if the requested element cannot be found.

**Call relations**: BrowserContent.tree relies on this contract after it has chosen the right tab. The actual page reader implementation lives elsewhere, but this method is the doorway BrowserContent uses to get the page structure.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This defines the promise that a page reader can turn a browser page into markdown, which is plain text with lightweight formatting. It is useful when the caller wants readable page content instead of a detailed element tree.

**Data flow**: It receives a browser tab and is expected to read the page behind that tab. It returns a markdown string representing the page content.

**Call relations**: BrowserContent.get_page_text uses this contract when a caller asks for the page’s readable text. The protocol keeps BrowserContent independent from the specific browser-reading implementation.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This defines the promise that a browser session can provide a page tab, either the current one or a specific tab by ID. It gives content-reading code a concrete page to inspect.

**Data flow**: It receives an optional tab ID. It uses that ID, or the session’s current context when no ID is given, and returns a PageTab object representing the selected browser tab.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this first, before reading anything from the page. It is the step that turns a caller’s optional tab choice into the tab object used by the rest of the flow.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This defines the promise that a browser session can provide the object that actually reads page content. It separates “which tab are we using?” from “how do we read that tab?”

**Data flow**: It takes no direct input beyond the session itself. It returns a BrowserContentPageReader, which can then produce a page tree or markdown text.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text use this after choosing a tab. The returned reader does the lower-level work of extracting content from the browser page.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This defines the promise that a browser session can report useful details about a tab, such as metadata callers may need alongside the page text. It lets text results carry context about where they came from.

**Data flow**: It receives a tab-like object and reads session knowledge about that tab. It returns a JSON-style dictionary of tab information.

**Call relations**: BrowserContent.get_page_text adds this information to its response after it reads the markdown. That means the caller gets both the page text and identifying details about the tab.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This gets a structured text snapshot of a browser page or a specific part of it. Callers use it when they need to inspect page elements in a way that is more precise than plain text.

**Data flow**: It starts with a JSON-style argument dictionary and an element filter. It reads the optional tab ID and reference ID, converts the tab ID into a number when possible, asks the browser session for that tab, then asks the page reader for a tree. If the reader cannot find the referenced element, it returns a clear message saying so; otherwise it returns the tree text.

**Call relations**: BrowserContent.read_page calls this when returning a page tree to a user or tool. BrowserContent.find also calls it first, because searching needs a page tree to search through. It delegates tab-ID cleanup to _tab_id and delegates the actual page reading to the browser session’s page reader.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns a page tree in a compact response suitable for a tool call or API response. It also limits the size so a very large web page does not overwhelm the caller.

**Data flow**: It receives a JSON-style argument dictionary. It reads the requested filter, accepts only known filter values, falls back to “all” when the value is missing or invalid, then asks BrowserContent.tree for the page structure. It returns the tree cut down to the maximum allowed size and a flag showing whether anything was cut off.

**Call relations**: This is a public-facing wrapper around BrowserContent.tree. It is used when the caller wants a structured view of the page, while BrowserContent.tree does the shared work of choosing the tab and reading the tree.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the current page as readable markdown text, along with information about the browser tab. It is for situations where the caller wants the article-like content of the page rather than its element structure.

**Data flow**: It receives a JSON-style argument dictionary, reads and converts the optional tab ID, asks the browser session for that tab, and asks the page reader for markdown. It returns the markdown shortened to the maximum allowed size, a flag saying whether it was shortened, and extra tab information from the browser session.

**Call relations**: This function follows the same tab-selection pattern as BrowserContent.tree, using _tab_id to normalize the tab ID. Instead of asking for a tree, it asks the page reader for markdown and then adds tab details before returning the result.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page tree for things matching a user’s query. It can use a simple built-in matcher, or, if supplied, a completer function that can make a smarter judgment from a prompt and the page tree.

**Data flow**: It receives a JSON-style argument dictionary and optionally a FindCompleter. It extracts the query as a required string, reads the full page tree, and then searches it. Without a completer, it parses matches directly from the tree and notes whether there may be more results. With a completer, it sends a prompt plus a shortened tree to the completer and resolves the reply back into concrete matches. It returns the matches and a human-readable summary.

**Call relations**: This function builds on BrowserContent.tree because searching starts with the same structured page snapshot used by page-reading tools. It hands simple searches to parse_tree_matches, AI-assisted searches to the provided completer followed by resolve_find_reply, and uses format_matches to produce a readable summary of what was found.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a caller-provided tab ID into either an integer or no tab ID at all. It lets the rest of the file accept tab IDs that arrive as numbers or strings in JSON-style input.

**Data flow**: It receives a JSON value that may be an integer, float, string, empty value, or something else. Integers pass through, floats and non-empty strings are converted to integers, and anything missing or unsupported becomes None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a tab. It keeps tab-ID cleanup in one place so those higher-level functions can focus on reading page content.

*Call graph*: called by 2 (get_page_text, tree).


### Coordinate translation
Coordinate utilities map between browser-window pixels and resized model-vision coordinate spaces for accurate screen actions.

### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `browser action planning and input dispatch`

A browser automation system often asks a vision model, such as Claude or Gemini, to look at a screenshot and point to something on the page. The hard part is that the model may not see the screenshot at the browser’s original size. Some providers shrink large images before processing them, and some report positions on their own fixed grid. If the system used those model-reported coordinates directly as browser pixels, clicks could be badly misplaced.

This file is the small “map scale” for that problem. It defines simple Size and Coord records, then provides helper functions to convert points back and forth. For Claude-style models, it first works out the largest screenshot size that avoids provider-side downscaling: no edge longer than 1568 pixels and no more than about 1.15 million pixels total. For Gemini, it knows that coordinates are reported on a fixed 0-to-1000 square grid, no matter how large the screenshot is.

The important idea is proportional scaling. If the model says “x is halfway across the image,” this file turns that into “x is halfway across the browser viewport.” Like reading a paper map, it converts from map inches to real-world miles, so the automation can dispatch input to the correct browser pixel.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: Works out the screenshot size that a Claude-family vision model can receive without the server shrinking it further. This matters because later coordinate math only stays accurate if the system knows the exact image size the model is reasoning over.

**Data flow**: It receives the browser viewport size. It first shrinks the width and height proportionally if either side is too long, then shrinks again if the total number of pixels is still too high. It returns a new Size containing the final width and height that should be safe for the model.

**Call relations**: When no custom model coordinate size is provided, effective_model_size calls this function to decide the model’s working image size. The returned Size becomes the reference scale used by both browser-to-model and model-to-browser coordinate conversion.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: Chooses the coordinate space that should be treated as the model’s view of the page. It uses an explicit override when one is supplied, otherwise it computes the screenshot size from the viewport.

**Data flow**: It receives the real browser viewport size and, optionally, a model-specific size. If the optional size is present, it returns that unchanged. If not, it passes the viewport to compute_screenshot_dimensions and returns the computed Size.

**Call relations**: Both model_to_viewport and viewport_to_model call this first so they agree on the same scale. It is the shared decision point that hides whether the model uses normal screenshot pixels or a special coordinate space.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point reported by the model into actual browser viewport pixels. This is used before sending a click, tap, or similar input to the browser.

**Data flow**: It receives a model-space coordinate, the real viewport size, and optionally a model coordinate size. It asks effective_model_size what scale the model used, then multiplies x and y proportionally from model size to viewport size. It returns a new Coord in browser pixels.

**Call relations**: This function sits at the handoff from model reasoning to browser action. After effective_model_size supplies the model’s coordinate scale, model_to_viewport creates the Coord that downstream input-dispatch code can use to act on the real page.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: Identifies whether a given model name needs a special coordinate system. In particular, it marks Gemini models as using a fixed 1000-by-1000 grid instead of screenshot pixels.

**Data flow**: It receives a model name, or no name. If the name contains “gemini” in any letter case, it returns a Size of 1000 by 1000. Otherwise it returns None, meaning the rest of the code should use the computed screenshot dimensions.

**Call relations**: This function is the model-specific rule book for coordinate size. Its result can be passed as the optional model size used by effective_model_size, which then feeds the conversion functions.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a real browser pixel position back into the coordinate system the model uses. This is useful when the system needs to describe or compare browser positions in the same scale as the model’s screenshot view.

**Data flow**: It receives a browser viewport coordinate, the viewport size, and optionally a model coordinate size. It asks effective_model_size for the model’s scale, then multiplies x and y proportionally from viewport size to model size. It returns a new Coord in model-space units.

**Call relations**: This is the reverse path of model_to_viewport. It also relies on effective_model_size, so both directions use the same understanding of the model’s coordinate space.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### Accessibility tree lookup
Lookup helpers and page readers convert live pages into model-readable accessibility text and resolve model references back to actionable browser elements.

### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

This file is the consumer side of a simple text format used to describe browser elements. A page is represented as lines like a list: each line may say the element's role, visible name, internal reference, and screen coordinates. The code reads those lines, extracts the useful parts, and uses them to answer “find this element” requests.

There are two main paths. The first is a direct local search: split the user's query into plain words, then keep tree entries whose text contains all those words. The second is for replies from a language model. The file includes a prompt telling the model how to answer with element references, but it does not blindly trust that answer. Instead, it checks every returned reference against the real tree and fills in role, name, and coordinates from the tree itself. This matters because a model might misquote or invent a reference; this file acts like a ticket checker at a door and only lets through references that really exist.

Finally, the file formats matches back into readable text, including a “more matches exist” hint when needed. Without this file, browser element search would be harder to trust and harder to present clearly.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function reads the text accessibility tree and pulls out the element records that can be searched or returned. It keeps only lines that have both a recognizable element shape and a valid reference.

**Data flow**: It takes the full tree text as input. For each line, it looks for the role, optional name, required reference, and optional x/y coordinates. It returns a list of small dictionaries containing the reference, role, name, coordinates, and a lowercase copy of the original line for searching.

**Call relations**: This is the shared doorway into the tree data. parse_tree_matches calls it before doing a simple word search, and resolve_find_reply calls it before checking whether a model's suggested references are real.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This small helper builds the standard match object used by the rest of the file. It makes sure every match has the same shape: reference, role, name, coordinates, and reason.

**Data flow**: It receives one parsed tree entry and a short reason string. It copies the stable element details from the entry, adds the reason, and returns a new dictionary ready to be shown or passed along.

**Call relations**: parse_tree_matches uses it when a tree entry matches the user's query directly. resolve_find_reply uses it after it has confirmed that a reference from a reply really exists in the tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple built-in search over the accessibility tree without needing a language model. It is useful for finding elements whose tree line contains all meaningful words from the query.

**Data flow**: It takes the tree text and the user's query. It lowercases the query, extracts word-like terms longer than one character, parses the tree into entries, and keeps entries whose lowercase line contains every term. It stops after the configured maximum number of results and returns those matches.

**Call relations**: This function depends on tree_entries to turn raw tree text into searchable records. For each successful hit, it asks _match_payload to package the result in the common match format.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function turns a suggested find reply into trustworthy matches by checking it against the actual tree. Its key job is to prevent made-up or stale element references from spreading further into the system.

**Data flow**: It takes a reply string and the original tree text. First it builds a lookup table of real tree entries by reference. Then it reads the reply line by line, ignores empty or unusable lines, notices NO_MATCHES and MORE markers, extracts references, removes duplicates, and keeps only references present in the real tree. It returns the verified matches plus a true-or-false flag saying whether the reply claimed there are more results.

**Call relations**: This function is used after an outside search answer, such as one from a language model, has been produced. It calls tree_entries to know what references are valid, and _match_payload to create safe match records using the tree's own role, name, and coordinates rather than trusting the reply.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns structured match records into a short human-readable list. It is meant for displaying results clearly, with references, element labels, coordinates, and optional reasons.

**Data flow**: It receives a list of match dictionaries and an optional flag saying whether more matches exist. It builds one display line per match, adds the reason when present, appends a refinement hint if there are more results, and returns the final text block.

**Call relations**: This is the final presentation step after matches have already been found or verified. Other parts of the system can call it when they need to show the search result back to a person or to another text-based component.


### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A web page is visually rich, but a model needs a clear list of meaningful items: buttons, links, inputs, headings, text, images, and frames. This file builds that view from Chrome's browser debugging API, called CDP (Chrome DevTools Protocol), which lets code ask the browser for page structure and element positions.

The file combines two browser views. One is the DOM snapshot, which gives element ids, screen boxes, iframe links, image sources, input types, and cursor style. The other is the accessibility tree, which gives human-facing roles and names, like button "Submit" or textbox "Search". The code joins them together using browser node ids, like matching a seating chart to a guest list.

It then renders the result in two ways. `render_page` produces an action-oriented tree with stable refs such as `e12` or `f1e3`, center coordinates, and useful state like checked or disabled. `render_markdown` produces a reading view with headings, links, bullets, images, and paragraphs.

The file also understands iframes, including out-of-process iframes that Chrome runs in separate targets. Without this file, the rest of the system would not know what is on the page, which elements are safe to refer to, or where to click.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference, such as `e12` or `f1e3`, into its frame prefix and backend element id. This is how text returned to the model is later tied back to a real browser node.

**Data flow**: It receives a ref string → checks that it matches the expected pattern → returns the frame prefix and numeric backend id, or `None` if the string is not a real ref.

**Call relations**: When rendering a subtree, `render_page` uses this to understand a requested starting ref. When the model asks to act on a ref, `BrowserPage.resolve_ref` uses it before looking up the frame that owns the element.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of a CDP request sender. CDP is Chrome DevTools Protocol, the command channel used to ask the browser for snapshots, geometry, accessibility data, and other facts.

**Data flow**: A caller provides a browser method name, optional parameters, and optionally a session id → the implementation sends that command to Chrome → it returns a JSON-like dictionary response.

**Call relations**: `fetch_target` relies on this interface to gather page structure. `Settle._flush_page_tasks` in another file also uses it to talk to the browser.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a short, repeatable number for a frame id so refs can include compact frame prefixes like `f1`. This keeps iframe element references readable while still distinguishing frames.

**Data flow**: It receives a browser frame id → maps it to a sequence number for the current tab → returns that number.

**Call relations**: `BrowserPage._snapshot_oop` calls it when naming an out-of-process iframe snapshot, so refs inside that iframe get their own prefix.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how page code gets the CDP connection for the current browser session. It is the doorway from this page logic to the live browser.

**Data flow**: It reads the session object → returns an object that can send CDP commands.

**Call relations**: The `BrowserPage` methods use this connection when taking snapshots, resolving iframe sessions, and finding the point to click for a ref.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Defines the setup step needed after attaching to a new browser target, such as an out-of-process iframe. It prepares that new CDP session so later commands work consistently.

**Data flow**: It receives a CDP session id → performs session initialization outside this protocol definition → returns when the session is ready.

**Call relations**: `BrowserPage._oop_session` calls this after attaching to an iframe target and before caching the session id for reuse.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely converts a JSON value into a floating-point number. It avoids crashes when browser data is missing or not numeric by using a default.

**Data flow**: It receives a value and a default → if the value is an int or float, it converts it to `float` → otherwise it returns the default.

**Call relations**: `_parse_document` uses it for scroll positions and layout bounds. `fetch_target` uses it for the browser's device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one HTML attribute in Chrome's compact snapshot format. Chrome stores attribute names and values as indexes into a shared string table, so this function translates that into a normal string lookup.

**Data flow**: It receives the shared strings table, a flat list of attribute indexes, and an attribute name → scans name/value pairs → returns the attribute value string if found, otherwise `None`.

**Call relations**: `_parse_document` calls this when it needs input types, image sources, and iframe sources from DOM nodes.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Extracts useful geometry and iframe facts from one document inside a Chrome DOM snapshot. It turns Chrome's dense snapshot data into a simpler record the rest of this file can use.

**Data flow**: It receives one raw document snapshot, the shared strings table, and the device pixel ratio → reads node ids, layout boxes, scroll offsets, styles, attributes, and child-document links → returns a `_RawDoc` with backend ids, element geometry, iframe mappings, and special node details.

**Call relations**: `parse_snapshot` calls this once for each document in the snapshot. It delegates small conversions to `_float` and `_attr`, and uses wire validation helpers to reject malformed browser data.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome's full DOM snapshot into per-frame document data with coordinates in one shared page space. This is what lets elements inside iframes get meaningful positions on the main page.

**Data flow**: It receives the browser snapshot, device pixel ratio, and a starting origin → parses each document → walks iframe parent-child links to accumulate each child document's offset → returns `DocData` objects with adjusted geometry, in-page iframe children, and out-of-process iframe placeholders.

**Call relations**: `fetch_target` calls this after capturing the DOM snapshot. It builds on `_parse_document` and produces the geometry that later gets joined with accessibility nodes.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Collects everything needed to understand one browser target: DOM geometry plus accessibility trees for its frames. A target may be the main page or a separately attached iframe.

**Data flow**: It receives a CDP connection, session id, ref-prefix rules, and an origin → enables browser snapshot APIs → captures DOM layout and device scale → parses geometry → asks for accessibility trees for each document → returns a `FrameSnapshot` tree for that target.

**Call relations**: `BrowserPage._snapshot_target` calls this as the core snapshot step. It sends CDP commands through `Cdp.send`, uses `parse_snapshot` for DOM data, and builds `FrameSnapshot` objects for rendering.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Pulls the plain value out of an accessibility-tree value object. Accessibility nodes wrap values in small dictionaries, and this function makes them easy to read.

**Data flow**: It receives a JSON value → if it is a dictionary, it reads its `value` field → returns that value as text, or an empty string when no useful value exists.

**Call relations**: `render_page.render_node` and `render_markdown.walk` use it to read roles and names from accessibility nodes before deciding what to show.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up one named property on an accessibility node, such as `checked`, `disabled`, `url`, or `level`. These properties add important meaning beyond the role and name.

**Data flow**: It receives an accessibility node and a property name → scans the node's property list → unwraps the property's value if needed → returns the value, or `None` if the property is absent.

**Call relations**: `_format_extras` uses it to print state details. Both page renderers use it to skip hidden nodes and preserve structure such as headings and links.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node can be hidden from the action tree. Empty containers like generic groups often add clutter but no useful target.

**Data flow**: It receives a node, its role, and its name → checks whether the role is a skippable container with no name and no important state → returns `True` when children should be shown without printing the container itself.

**Call relations**: `render_page.render_node` calls this while simplifying the accessibility tree for model use.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so rendered page output stays readable and compact. It preserves the start of the text and marks trimming with an ellipsis.

**Data flow**: It receives text and a maximum length → returns the original text if short enough → otherwise returns a shortened version ending in `…`.

**Call relations**: `_image_name`, `_format_extras`, and `render_page.render_node` call it to keep names, values, and extra properties from overwhelming the page snapshot.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a fallback label for an image from its file name when the accessibility tree has no image name. This gives the model a clue about otherwise unnamed images.

**Data flow**: It receives an image source URL → extracts the last path segment → returns a shortened filename if it looks like a file, otherwise an empty string.

**Call relations**: `render_page.render_node` uses it for unnamed images in the action tree. `render_markdown.walk` uses it for image entries in the reading view.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the small state suffix printed after a node in the action tree. This includes useful details like input type, value, checked state, expanded state, disabled state, and safe URLs.

**Data flow**: It receives an accessibility node and optional geometry/details from the DOM snapshot → reads selected accessibility properties and DOM hints → returns a formatted text suffix, or an empty string if there are no extras.

**Call relations**: `render_page.render_node` appends this output to each visible line. It relies on `_ax_property` and `_truncate` so the state is accurate but compact.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that owns a ref prefix. Prefixes are how refs distinguish elements in the main page from elements inside iframes.

**Data flow**: It receives the root frame snapshot and a prefix → searches the frame tree recursively → returns the matching frame or `None`.

**Call relations**: `render_page` calls it when asked to render only the subtree under a particular ref.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node that corresponds to a browser backend DOM node id. This bridges the ref id back into the accessibility tree.

**Data flow**: It receives a frame snapshot and backend node id → scans accessibility nodes for a matching `backendDOMNodeId` → returns the accessibility node id or `None`.

**Call relations**: `render_page` uses it after `split_ref` and `_frame_by_prefix` when rendering from a specific referenced element.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Renders a `FrameSnapshot` as an action-focused tree for the model. The output shows roles, names, refs, approximate center coordinates, and useful state.

**Data flow**: It receives a snapshot, viewport size, optional model-size scaling, filter settings, depth limit, and optional starting ref → walks the accessibility tree and iframe children → returns newline-separated text, or `None` if a requested ref cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a fresh snapshot. It uses `split_ref`, `_frame_by_prefix`, and `_node_by_backend` for subtree rendering, and `effective_model_size` to scale browser coordinates into model coordinates.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats the center point of an element for the action tree. The center is usually the safest simple point to click.

**Data flow**: It receives optional element geometry → if bounds exist, it scales the center into model coordinates → returns text like ` (x=120,y=45)`, or an empty string when no bounds exist.

**Call relations**: This helper is used inside `render_page.render_node` when a visible node line is built.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame's accessibility tree at the iframe node where it belongs. This makes the rendered page read like one tree instead of separate disconnected documents.

**Data flow**: It receives a frame, an optional iframe backend id, and a depth → finds the child frame attached to that iframe → starts rendering the child root at the same place in the output.

**Call relations**: `render_page.render_node.descend` calls this after rendering normal children, so iframe contents appear under their iframe.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Renders one accessibility node and then its descendants. This is the main decision point for what appears in the action tree and what is treated as clutter.

**Data flow**: It receives a frame, accessibility node id, indentation depth, and parent name → skips hidden, repeated, unhelpful, too-deep, or already-visited nodes → formats useful nodes with role, name, ref, coordinates, and extras → recurses into children and iframe content.

**Call relations**: This nested worker powers `render_page`. It calls helpers such as `_ax_value`, `_ax_property`, `_should_skip`, `_format_extras`, `_image_name`, and `_truncate` to turn raw browser data into readable lines.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues rendering below the current node without repeating traversal code. It walks both ordinary child nodes and iframe children.

**Data flow**: It receives the child indentation depth and parent name → renders each child accessibility node → splices in any child frame attached to the current DOM node.

**Call relations**: `render_page.render_node` uses it when a node is transparent and when a visible node has been printed.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Renders a `FrameSnapshot` as a reading view in Markdown. This is useful when the model needs page content and structure rather than click targets.

**Data flow**: It receives the root frame snapshot → walks accessibility nodes and iframe children → groups inline text into paragraphs, preserves headings and links, emits bullets and images → returns Markdown text.

**Call relations**: `BrowserPage.markdown` calls this after taking a snapshot. It uses the same frame-splicing idea as `render_page`, so iframe content is included in reading order.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one finished Markdown block while avoiding immediate duplicates. A block is a paragraph, heading, bullet, or image line.

**Data flow**: It receives text → trims whitespace → appends it to the block list only if it is non-empty and not the same as the previous block.

**Call relations**: This helper is used inside `render_markdown` by `flush` and `walk` whenever a complete piece of readable content is ready.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns accumulated inline words into one paragraph block. It is like ending a sentence group before starting a heading, list item, or new block.

**Data flow**: It reads the current inline text buffer → joins the pieces with spaces and emits the result → clears the buffer.

**Call relations**: `render_markdown.walk` calls it when crossing block boundaries or before emitting structured Markdown.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node for the Markdown renderer. It decides whether the node contributes a heading, link, list item, image, paragraph text, or nothing.

**Data flow**: It receives a frame, accessibility node id, and parent name → ignores hidden or repeated text → appends inline content or emits blocks based on role → walks children and then iframe content → updates the Markdown block and inline buffers.

**Call relations**: This nested worker powers `render_markdown`. It calls `_ax_value`, `_ax_property`, and `_image_name` to interpret accessibility nodes in reader-friendly terms.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Produces the model's action-oriented view of a tab. This is the public method used when the system wants to show the model what it can interact with.

**Data flow**: It receives a tab, filter type, and optional ref → takes a fresh snapshot → renders it through `render_page` using the browser viewport and model size → returns the text tree or `None` if the requested ref is invalid.

**Call relations**: It calls `BrowserPage.snapshot` first so refs and geometry are current, then hands the snapshot to `render_page`.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Produces a reading-oriented Markdown view of a tab. This is useful for understanding article-like content or page text without action metadata.

**Data flow**: It receives a tab → takes a fresh snapshot → renders it through `render_markdown` → returns Markdown text.

**Call relations**: It calls `BrowserPage.snapshot` for current page data and then delegates the formatting to `render_markdown`.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Validates a model-provided browser ref and finds the frame that owns it. It protects the browser from invented or stale refs.

**Data flow**: It receives a tab and ref string → parses the ref → looks up the frame prefix in the tab's registered frame map → returns the frame node and backend element id, or raises `HallucinationError` with guidance to re-read the page.

**Call relations**: `BrowserPage.ref_point` calls this before trying to scroll to or locate an element. It uses `split_ref` for the syntax check.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a clickable center point for a referenced browser element. This lets a model-visible ref become a real screen coordinate.

**Data flow**: It receives a tab and ref → resolves the ref to a frame and backend id → asks Chrome to scroll the element into view → asks for its content quadrilateral or box model → averages the corners and adds the frame origin → returns integer x/y coordinates, or raises `HallucinationError` if the ref no longer works.

**Call relations**: It starts with `BrowserPage.resolve_ref`, sends CDP commands through the browser connection, and uses `_coord_float_or_default` to safely read coordinate values.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Takes a complete fresh snapshot of the tab and records which frame prefix maps to which browser frame. This keeps refs returned to the model resolvable afterward.

**Data flow**: It receives a tab → snapshots the main target → clears the tab's old ref-frame map → registers every frame in the new snapshot → returns the root `FrameSnapshot`.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates snapshot work to `_snapshot_target` and ref registration to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one browser target and optionally fills in out-of-process iframe children. A target is a separately addressable browser context, such as the main page or a cross-origin iframe.

**Data flow**: It receives a tab, session id, root ref prefix, origin, and recursion depth → fetches the target snapshot → if the depth limit allows, attaches snapshots for out-of-process iframes → returns the completed frame snapshot.

**Call relations**: `BrowserPage.snapshot` uses it for the main page. `BrowserPage._snapshot_oop` uses it recursively for iframe targets. It calls `fetch_target` for the core data collection and `_attach_oop_frames` for iframe expansion.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Finds iframe placeholders that belong to separate browser targets and attaches their snapshots into the frame tree. This makes cross-origin or out-of-process iframe content visible to the model.

**Data flow**: It receives a tab, root frame, and depth → walks the existing frame tree → for each out-of-process iframe backend id, tries to snapshot that child → adds successful child snapshots under the iframe node.

**Call relations**: `BrowserPage._snapshot_target` calls this after a normal target snapshot. It delegates each iframe attempt to `BrowserPage._snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe if Chrome can describe and attach to it. Failures are logged and skipped so one broken iframe does not lose the whole page.

**Data flow**: It receives the owning tab, parent frame, iframe backend id, and depth → asks Chrome which frame id the iframe uses → gets or creates a CDP session for that frame → computes the iframe origin from its bounds → snapshots the iframe target recursively → returns the child `FrameSnapshot`, or `None` on recoverable failure.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each out-of-process iframe. It uses `BrowserPage._oop_session`, `BrowserPage._snapshot_target`, and `PageTab.frame_seq` to attach, name, and snapshot the child frame.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Builds the lookup table that turns ref prefixes back into browser frame sessions. This is why a later action on `f1e3` can find the right iframe.

**Data flow**: It receives a tab and a frame snapshot → stores the frame id, session id, and origin under the frame prefix → repeats for every child frame.

**Call relations**: `BrowserPage.snapshot` calls this after taking a fresh snapshot, before refs are used by `BrowserPage.resolve_ref` or `BrowserPage.ref_point`.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing one if already attached. This avoids repeatedly attaching to the same browser target.

**Data flow**: It receives a frame id → checks the browser session cache → if missing, asks Chrome to attach to that target → initializes the new session → stores and returns the session id, or returns `None` if attach fails.

**Call relations**: `BrowserPage._snapshot_oop` calls this before it can snapshot a separate iframe target.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts a coordinate value from browser JSON into a float, with a default for missing values. It rejects values that are neither numbers nor numeric strings.

**Data flow**: It receives a JSON value and default → returns a float for numeric values or numeric strings → returns the default for `None` → raises `ValidationError` for invalid coordinate data.

**Call relations**: `BrowserPage.ref_point` uses this while averaging the four corners returned by Chrome for an element.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).
